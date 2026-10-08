const assert = require('node:assert/strict');
const fs = require('node:fs');
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
const source = fs.readFileSync(__dirname + '/run_connected.js', 'utf8');

async function scenario(rateLimited, resume = false) {
  let calls = 0, launched = false, published = false, payload, saved;
  const previous = {status: 'running', session: 42, bridge: '/fixture/source-crawl/data/.bridge-test', crawlArgs: [], requests: 2, failures: 0};
  const tools = {
    async exec_command({cmd}) {
      if (cmd === 'pwd') return {exit_code: 0, output: '/fixture'};
      if (cmd.includes('print(p.read_text()')) return {exit_code: 0, output: JSON.stringify(resume ? previous : null)};
      if (cmd.includes('crawl.py')) { launched = true; return {session_id: 42, output: ''}; }
      if (cmd.includes('*.request.json')) return {exit_code: 0, output: JSON.stringify(published ?
        {queue: [], done: {exit_code: 0}, progress: {sources_completed: 1}} :
        {queue: [{id: 'request123', url: 'https://example.com/news'}], done: null})};
      if (cmd.includes('os.replace')) { published = true; return {exit_code: 0, output: ''}; }
      return {exit_code: 0, output: ''};
    },
    async mcp__codex_apps__firecrawl_firecrawl_scrape(args) {
      calls++;
      assert.equal(args.maxAge, 0);
      assert.equal(args.proxy, 'basic');
      if (rateLimited && calls === 1) return {isError: true, structuredContent: {error: 'Rate limit exceeded'}};
      return {structuredContent: {rawHtml: '<html><script>tracking()</script><article><p>Bitcoin news</p></article></html>', metadata: {statusCode: 200, sourceURL: args.url}}};
    },
    async apply_patch(patch) {
      const value = JSON.parse(patch.split('\n').find(line => line.startsWith('+{')).slice(1));
      if (patch.includes('.response.json.tmp')) payload = value;
      if (patch.includes('connected-state.json')) saved = value;
      return {};
    },
    async write_stdin() { throw new Error('Resume must not depend on an old tool session'); }
  };
  await new AsyncFunction('tools', 'text', 'notify', 'setTimeout', source)(tools, () => {}, () => {}, callback => { callback(); });
  assert.equal(launched, !resume);
  assert.equal(payload.status, 200);
  assert.match(payload.body, /Bitcoin/);
  assert.doesNotMatch(payload.body, /tracking/);
  assert.equal(calls, rateLimited ? 2 : 1);
  assert.equal(saved.status, 'complete');
  assert.equal(saved.requests, resume ? 3 : 1);
}

(async () => {
  await scenario(false);
  await scenario(true);
  await scenario(false, true);
  let started = false;
  await assert.rejects(new AsyncFunction('tools', 'text', 'notify', 'setTimeout', source)({exec_command: async () => {started = true;}}, () => {}, () => {}, setTimeout), /unavailable/);
  assert.equal(started, false, 'Missing connector must not leave an orphan worker');
  console.log('4 connected runner integration tests passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
