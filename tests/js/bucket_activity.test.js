/* Tests _bucketActivity, extracted straight from monitor.py. This is the
 * session activity sparkline's bucketing logic: raw (epoch_s, state)
 * samples -> fixed-width time buckets, carrying forward the last known
 * state so a sparse sample doesn't leave gaps. */
const { readMonitorSource, extractFunction } = require('./lib/extract');
const { Checker } = require('./lib/check');

const src = readMonitorSource();
eval(extractFunction(src, '_bucketActivity'));

const c = new Checker();
const NOW = 10000;

// 1. empty history -> every bucket is null (no data yet)
const empty = _bucketActivity([], NOW, 10, 1000);
c.check('empty history -> all buckets null', empty.length === 10 && empty.every(b => b === null));

// 2. a single sample near the start of the window -> carries forward through every later bucket
const single = _bucketActivity([[NOW - 950, 'active']], NOW, 10, 1000);
c.check('single early sample carries forward to fill all buckets', single.every(b => b === 'active'));

// 3. buckets before the first sample are null; buckets after carry the state
const midSample = _bucketActivity([[NOW - 500, 'waiting']], NOW, 10, 1000);
// bucket size = 100s; sample at NOW-500 falls in bucket index 5 (covers [NOW-500, NOW-400))
c.check('buckets before the first sample are null', midSample.slice(0, 5).every(b => b === null));
c.check('buckets from the sample onward carry its state', midSample.slice(5).every(b => b === 'waiting'));

// 4. a state transition partway through is reflected at the right point
// (bucket size 100s; a sample exactly at a bucket's bEnd boundary is
// consumed by the NEXT bucket, since the loop condition is a strict <)
const transition = _bucketActivity([[NOW - 900, 'active'], [NOW - 400, 'idle']], NOW, 10, 1000);
c.check('bucket before the first sample is consumed is still null', transition[0] === null);
c.check('early buckets show the first state once consumed', transition[1] === 'active' && transition[4] === 'active');
c.check('later buckets show the transitioned state', transition[9] === 'idle');

// 5. exactly the right number of buckets is always returned
c.check('returns exactly numBuckets entries', _bucketActivity([], NOW, 20, 7200).length === 20);

// 6. multiple samples within the same bucket -- the LAST one wins
const sameBucket = _bucketActivity([[NOW - 950, 'active'], [NOW - 920, 'waiting'], [NOW - 910, 'idle']], NOW, 10, 1000);
c.check('multiple samples in one bucket resolve to the most recent', sameBucket[0] === 'idle');

// 7. a sample exactly at "now" doesn't get included (windowS boundary is exclusive at the far end
// by construction -- bEnd for the last bucket equals `now`, and the loop condition is strict <)
const atNow = _bucketActivity([[NOW - 1, 'active']], NOW, 10, 1000);
c.check('a sample just before "now" lands in the final bucket', atNow[9] === 'active');

c.finish();
