// Execute in a Codex functions.exec host, where `tools` is available.
// Example loader is in README.md. No local API key or external DNS is needed.
// `options` may override crawlArgs; all paths are resolved by Python.
const options = globalThis.SOURCE_CRAWL_OPTIONS || {};
const scrapeTool = tools.mcp__codex_apps__firecrawl_firecrawl_scrape;
if (!scrapeTool) throw new Error('Connected Firecrawl tool is unavailable in this host');
const location = await tools.exec_command({cmd: 'pwd', max_output_tokens: 300});
const root = options.root || location.output.trim();
const crawlArgs = options.crawlArgs || ['--max-articles', '5', '--discovery-pages', '0'];
// Use shell quoting, not JSON.stringify, to preserve shell metacharacters literally.
const quote = s => "'" + String(s).replace(/'/g, "'\\''") + "'";
const statePath = root + '/source-crawl/data/connected-state.json';
const readJsonScript = 'import json,sys\nfrom pathlib import Path\np=Path(sys.argv[1])\nprint(p.read_text() if p.exists() else "null")';
const readState = await tools.exec_command({cmd: 'python3 -c ' + quote(readJsonScript) + ' ' + quote(statePath), max_output_tokens: 2000});
let state = JSON.parse(readState.output.trim() || 'null');
if (!state || state.status !== 'running') {
  const bridge = root + '/source-crawl/data/.bridge-' + Date.now();
  const command = ['python3', root + '/source-crawl/crawl.py', 'crawl', '--backend', 'connected', '--bridge', bridge, ...crawlArgs].map(quote).join(' ');
  const launched = await tools.exec_command({cmd: command, workdir: root, yield_time_ms: 1000, max_output_tokens: 1500});
  if (!launched.session_id) { text(launched); return; }
  state = {status: 'running', session: launched.session_id, bridge, crawlArgs, requests: 0, failures: 0};
}
const bridge = state.bridge;
const session = state.session;
let requests = state.requests, failures = state.failures, lastStatus = Date.now();
const readScript = `import json,sys\nfrom pathlib import Path\np=Path(sys.argv[1])\nprint(json.dumps({'queue':[json.loads(f.read_text()) for f in sorted(p.glob('*.request.json'))], 'done':json.loads((p/'done.json').read_text()) if (p/'done.json').exists() else None, 'progress':json.loads((p/'progress.json').read_text()) if (p/'progress.json').exists() else None}))`;
async function saveState() {
  state.requests = requests; state.failures = failures;
  state.updated_at = new Date().toISOString();
  await tools.apply_patch('*** Begin Patch\n*** Add File: ' + statePath + '\n+' + JSON.stringify(state) + '\n*** End Patch');
}
await saveState();
await tools.apply_patch('*** Begin Patch\n*** Add File: ' + bridge + '/host.json\n+' + JSON.stringify({status: 'running'}) + '\n*** End Patch');
const deadline = Date.now() + (options.durationMs || 90000);
while (true) {
  const read = await tools.exec_command({cmd: 'python3 -c ' + quote(readScript) + ' ' + quote(bridge), max_output_tokens: 1500});
  if (read.exit_code !== 0) throw new Error(read.output);
  const batch = JSON.parse(read.output.trim() || '{}');
  const queue = batch.queue || [];
  if (batch.done) {
    state.status = 'complete'; state.result = batch.done;
    await saveState();
    text({status: 'complete', ...batch.done, requests, transportFailures: failures});
    break;
  }
  for (const req of queue) {
    let response;
    const startedAt = Date.now();
    // Limited backoff for service rate limits; no publisher challenge bypass.
    for (let attempt = 0; attempt < 4; attempt++) {
      try {
        const result = await scrapeTool({url: req.url, formats: ['rawHtml'], onlyMainContent: false,
                                         maxAge: 0, proxy: 'basic', timeout: 30000});
        let doc = result.structuredContent;
        if (!doc || (!doc.rawHtml && !doc.data && !doc.metadata && !doc.error)) {
          const block = result.content?.find(c => c.type === 'text');
          try { doc = JSON.parse(block?.text || '{}'); }
          catch { doc = {error: block?.text || 'Invalid connector result'}; }
        }
        doc = doc.data || doc;
        if (result.isError || doc.error) {
          const reason = typeof doc.error === 'string' ? doc.error : JSON.stringify(doc.error || result.content);
          if (/rate limit/i.test(reason) && attempt < 3 && Date.now() - startedAt < 60000) {
            const reset = reason.match(/resets at (.+?)(?: \(|$)/i);
            let waitMs = Math.min(60000, 10000 * (attempt + 1));
            if (reset) {
              const resetTime = Date.parse(reset[1]);
              if (Number.isFinite(resetTime)) waitMs = Math.min(60000, Math.max(1000, resetTime - Date.now() + 1500));
            }
            notify({status: 'connector rate limit; retrying', seconds: Math.ceil(waitMs / 1000)});
            await new Promise(resolve => setTimeout(resolve, waitMs));
            continue;
          }
          response = {error: reason};
        } else {
          const metadata = doc.metadata || {};
          let body = doc.rawHtml;
          if (typeof body === 'string' && /<html[\s>]/i.test(body)) {
            body = body.replace(/<script\b([^>]*)>[\s\S]*?<\/script>/gi, (tag, attrs) => /application\/ld\+json/i.test(attrs) ? tag : '')
                       .replace(/<style\b[^>]*>[\s\S]*?<\/style>/gi, '');
          }
          const status = metadata.statusCode || 200;
          const type = /\/robots\.txt(?:\?|$)/.test(req.url) ? 'text/plain'
                       : req.url.includes('api.coinpaprika.com/') ? 'application/json'
                       : /\.xml(?:\?|$)/.test(req.url) ? 'application/xml' : 'text/html';
          // sourceURL is normally the requested address. Metadata.url, when present,
          // is used to surface a redirect rather than silently bypass robots checks.
          response = {status, body: body || '', content_type: type, url: metadata.url || metadata.sourceURL || req.url};
          if (!body && status < 400) response = {error: 'Connector returned no raw body'};
        }
      } catch (error) {
        response = {error: String(error)};
      }
      break;
    }
    response ||= {error: 'Connector retry limit reached'};
    if (response.error || response.status >= 400) failures++;
    const responsePath = bridge + '/' + req.id + '.response.json';
    const temporaryPath = responsePath + '.tmp';
    await tools.apply_patch('*** Begin Patch\n*** Add File: ' + temporaryPath + '\n+' + JSON.stringify(response) + '\n*** End Patch');
    const publishScript = 'import os,sys\nos.replace(sys.argv[1],sys.argv[2])';
    const published = await tools.exec_command({cmd: 'python3 -c ' + quote(publishScript) + ' ' + quote(temporaryPath) + ' ' + quote(responsePath), max_output_tokens: 200});
    if (published.exit_code !== 0) throw new Error('Failed to publish connector response: ' + published.output);
    requests++;
    await saveState();
  }
  if (Date.now() - lastStatus > 20000) {
    notify({status: 'connected crawl running', requests, transportFailures: failures, progress: batch.progress});
    lastStatus = Date.now();
  }
  if (Date.now() >= deadline) {
    await tools.apply_patch('*** Begin Patch\n*** Add File: ' + bridge + '/host.json\n+' + JSON.stringify({status: 'paused'}) + '\n*** End Patch');
    await saveState();
    text({status: 'resume_needed', requests, transportFailures: failures, progress: batch.progress});
    break;
  }
  if (!queue.length) await new Promise(resolve => setTimeout(resolve, 300));
}
