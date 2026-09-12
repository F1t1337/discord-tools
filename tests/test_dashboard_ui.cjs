const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'dashboard/app.js'), 'utf8');
const html = fs.readFileSync(path.join(root, 'dashboard/index.html'), 'utf8');

function fixture() {
  const nodes = new Map();
  const node = () => ({value:'', disabled:false, hidden:false, textContent:'', children:[],
    replaceChildren(...children) {this.children=children; this.value=children[0]?.value || '';}});
  const context = vm.createContext({
    $: id => {if (!nodes.has(id)) nodes.set(id,node()); return nodes.get(id);},
    number: String, date: String,
    element: (tag, text) => ({textContent:text, value:''}),
    exportBusy:false, uploadBusy:false,
  });
  vm.runInContext(source.slice(source.indexOf('function renderTokens(job)'), source.indexOf('function renderLogs(')), context);
  return {context, nodes, render: job => context.renderTokens(job)};
}

test('literal DOM references resolve', () => {
  const ids = new Set([...html.matchAll(/id="([^"]+)"/g)].map(m=>m[1]));
  for (const match of source.matchAll(/\$\('([^']+)'\)/g)) assert.ok(ids.has(match[1]), match[1]);
});
test('failed delivery is explicit and remains downloadable', () => {
  const f = fixture();
  f.render({finished_at:1,valid:1,invalid:0,deferred:0,delivery:'failed',worker_running:true,
    history:[{id:1,count:1,created_at:1,delivery:'failed'}]});
  assert.match(f.nodes.get('export-job').textContent,/не доставил/);
  assert.doesNotMatch(f.nodes.get('export-job').textContent,/Файл отправлен/);
  assert.equal(f.nodes.get('token-download').disabled,false);
});
test('stopped worker disables intake while history stays available', () => {
  const f = fixture();
  f.render({worker_running:false,history:[{id:1,count:1,created_at:1,delivery:'sent'}]});
  assert.equal(f.nodes.get('token-upload').disabled,true);
  assert.equal(f.nodes.get('token-export').disabled,true);
  assert.equal(f.nodes.get('token-download').disabled,false);
  assert.equal(f.nodes.get('token-worker-note').hidden,false);
});
test('polling preserves selected historical file during a new export', () => {
  const f = fixture();
  const history=[{id:2,count:1,created_at:2,delivery:'sent'},{id:1,count:1,created_at:1,delivery:'failed'}];
  f.render({history,worker_running:true});
  f.nodes.get('token-export-history').value='1';
  f.render({history,worker_running:true,running:true,done:0,total:3});
  assert.equal(f.nodes.get('token-export-history').value,'1');
  assert.equal(f.nodes.get('token-download').disabled,false);
  assert.equal(f.nodes.get('token-export').disabled,true);
});

test('network panel advises against increasing threads after 429', () => {
  const nodes = new Map();
  const context = vm.createContext({
    $: id => {if (!nodes.has(id)) nodes.set(id,{replaceChildren(){},textContent:''}); return nodes.get(id);},
    number:String, date:String, details(){}, element:()=>({append(){}}),
  });
  vm.runInContext(source.slice(source.indexOf('function renderNetwork(result)'), source.indexOf('async function proxyAction(')), context);
  context.renderNetwork({available:true,proxy_enabled:true,last_minute:{rate_limits:4},last_five_minutes:{requests:200}});
  assert.match(nodes.get('network-advice').textContent,/Не увеличивайте/);
});

test('network panel does not infer extra capacity from a small sample', () => {
  const nodes = new Map();
  const context = vm.createContext({
    $: id => {if (!nodes.has(id)) nodes.set(id,{replaceChildren(){},textContent:''}); return nodes.get(id);},
    number:String, date:String, details(){}, element:()=>({append(){}}),
  });
  vm.runInContext(source.slice(source.indexOf('function renderNetwork(result)'), source.indexOf('async function proxyAction(')), context);
  context.renderNetwork({available:true,proxy_enabled:true,uptime_seconds:5,last_minute:{},last_five_minutes:{requests:3}});
  assert.match(nodes.get('network-advice').textContent,/мало наблюдений/);
});
