const fs = require('fs');
const path = require('path');
const eventPath = process.env.BTW_EVENTS;
function emit(type, data = {}) {
  if (!eventPath) return;
  fs.appendFileSync(eventPath, JSON.stringify({ type, at: Date.now(), ...data }) + '\n');
}
function title(test) { return test.titlePath().slice(1).join(' › '); }
class Reporter {
  onBegin(config, suite) { emit('begin', { total: suite.allTests().length }); }
  onTestBegin(test) { emit('test_begin', { id: test.id, title: title(test) }); }
  onStepBegin(test, result, step) {
    if (step.category === 'test.step') emit('step_begin', { id: test.id, title: step.title });
  }
  onStepEnd(test, result, step) {
    if (step.category === 'test.step') emit('step_end', { id: test.id, title: step.title, duration: step.duration, error: step.error?.message || '' });
  }
  onTestEnd(test, result) {
    emit('test_end', { id: test.id, title: title(test), status: result.status, duration: result.duration,
      error: result.errors.map(e => e.message || String(e)).join('\n'),
      attachments: result.attachments.filter(a => a.path).map(a => ({ name: a.name, path: a.path, contentType: a.contentType })) });
  }
  onError(error) { emit('error', { error: error.message || String(error) }); }
  onEnd(result) { emit('end', { status: result.status }); }
}
module.exports = Reporter;
