/* Tiny shared assertion harness -- no framework, matching this project's
 * own "stay dependency-free" choice. Each test file creates one Checker,
 * calls .check(label, cond) for every assertion, then .finish() to print
 * the summary and set process.exitCode. */
class Checker {
  constructor() {
    this.pass = 0;
    this.fail = 0;
  }
  check(label, cond) {
    if (cond) {
      this.pass++;
      console.log(`PASS: ${label}`);
    } else {
      this.fail++;
      console.log(`FAIL: ${label}`);
    }
  }
  finish() {
    console.log(`\n${this.pass} passed, ${this.fail} failed`);
    process.exitCode = this.fail > 0 ? 1 : 0;
  }
}

module.exports = { Checker };
