const fs = require('fs');
const { stripVTControlCharacters } = require('node:util');
const eventPath = process.env.BTW_EVENTS;
function emit(type, data = {}) {
  if (!eventPath) return;
  fs.appendFileSync(eventPath, JSON.stringify({ type, at: Date.now(), ...data }) + '\n');
}
function title(test) { return test.titlePath().slice(1).join(' › '); }
function httpOrigin(value) {
  try {
    const url = new URL(value);
    return url.protocol === 'http:' || url.protocol === 'https:' ? url.origin : '';
  } catch {
    return '';
  }
}
function isCompletedCheck(step) {
  if (step.category !== 'expect' || step.duration < 0 || step.annotations?.some(item => item.type === 'skip')) {
    return false;
  }
  for (let parent = step.parent; parent; parent = parent.parent) {
    if (parent.category === 'expect') return false;
  }
  return true;
}
function timedStepName(step) {
  if (step.category === 'test.step') return step.title;
  if (step.category === 'pw:api' &&
      /^(?:page\.waitForResponse|Wait for response|Wait for event "response")$/i.test(step.title)) {
    return 'API 응답 대기';
  }
}
class Reporter {
  onBegin(config, suite) {
    const projects = config.projects.map(({ name, use }) => ({
      name,
      browser: use.channel || use.browserName || 'chromium',
      origin: httpOrigin(use.baseURL),
    }));
    emit('begin', { total: suite.allTests().length, projects });
  }
  onTestBegin(test, result) { emit('test_begin', { id: test.id, title: title(test), retry: result.retry }); }
  onStepBegin(test, result, step) {
    const name = timedStepName(step);
    if (name) emit('step_begin', { id: test.id, title: name });
  }
  onStepEnd(test, result, step) {
    const name = timedStepName(step);
    if (name) {
      emit('step_end', { id: test.id, title: name, duration: step.duration, error: step.error?.message || '' });
    } else if (isCompletedCheck(step)) {
      emit('check_end', {
        id: test.id,
        test: title(test),
        retry: result.retry,
        title: step.title,
        status: step.error ? 'failed' : 'passed',
        error: stripVTControlCharacters(step.error?.message || ''),
      });
    }
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
