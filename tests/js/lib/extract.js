/* Shared helper: pull a named top-level function's source text straight out
 * of monitor.py's embedded <script> block, so tests can never silently drift
 * from the real shipped code (the risk with this session's earlier throwaway
 * scratch tests, which sometimes hand-copied logic instead). Uses a
 * brace-counting scan rather than a single non-greedy regex, since a naive
 * "up to the first \n}" match breaks on any function whose body contains a
 * nested block that also happens to end a line with just "}". */
const fs = require('fs');
const path = require('path');

const MONITOR_PATH = path.join(__dirname, '..', '..', '..', 'monitor.py');

function readMonitorSource() {
  return fs.readFileSync(MONITOR_PATH, 'utf8');
}

function extractFunction(src, name) {
  const re = new RegExp(`(async\\s+)?function\\s+${name}\\s*\\(`);
  const m = re.exec(src);
  if (!m) throw new Error(`function ${name} not found in monitor.py`);
  const start = m.index;
  const openBrace = src.indexOf('{', m.index);
  if (openBrace === -1) throw new Error(`no body found for ${name}`);
  let depth = 0, end = -1;
  for (let j = openBrace; j < src.length; j++) {
    if (src[j] === '{') depth++;
    else if (src[j] === '}') {
      depth--;
      if (depth === 0) { end = j; break; }
    }
  }
  if (end === -1) throw new Error(`unbalanced braces extracting ${name}`);
  return src.slice(start, end + 1);
}

function extractFunctions(names) {
  const src = readMonitorSource();
  return names.map(name => extractFunction(src, name)).join('\n\n');
}

module.exports = { readMonitorSource, extractFunction, extractFunctions, MONITOR_PATH };
