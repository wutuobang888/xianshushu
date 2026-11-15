// 独立扩展后台回归：合成订单、模拟 Chrome API，不连接平台。
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../browser-extension');
const policy = require(path.join(root, 'policy.js'));
const receiver = {receiver_name:'测试买家',receiver_mobile:'13800000000',prov_name:'浙江省',
    city_name:'杭州市',area_name:'西湖区',address:'测试路1号'};
const target = id => ({order_id:id,order_no:'offline-'+id,receiver:{...receiver,receiver_name:'测试买家'+id}});
const localSender = {tab:{id:1},frameId:0,url:'http://127.0.0.1:5000/orders'};

function worker(session = {}, local = {}) {
    const tabs = new Map([[1,{id:1,url:localSender.url}]]);
    let next = 10, receive, alarm, removed, navigation;
    const storage = values => ({
        async get(key) {
            if (key === null) return structuredClone(values);
            return {[key]:structuredClone(values[key])};
        },
        async set(entries) {Object.assign(values,structuredClone(entries));},
        async remove(keys) {for (const key of [].concat(keys)) delete values[key];}
    });
    const chrome = {
        storage:{session:storage(session),local:storage(local)},
        runtime:{onMessage:{addListener(fn){receive=fn;}},onInstalled:{addListener(){}},onStartup:{addListener(){}}},
        tabs:{
            async create(options) {const tab={id:next++,...options};tabs.set(tab.id,tab);return tab;},
            async update(id,options){Object.assign(tabs.get(id),options);},
            async get(id){if(!tabs.has(id))throw new Error('missing tab');return tabs.get(id);},
            onRemoved:{addListener(fn){removed=fn;}}
        },
        alarms:{create(){},onAlarm:{addListener(fn){alarm=fn;}}}
        ,webNavigation:{onCreatedNavigationTarget:{addListener(fn){navigation=fn;}}}
    };
    let nonce = 0;
    const sandbox={chrome,Date,URL,Set,Map,setTimeout,crypto:{randomUUID:()=> 'nonce-'+(++nonce)},XssPolicy:policy,importScripts(){}};
    vm.runInNewContext(fs.readFileSync(path.join(root,'background.js'),'utf8'),sandbox);
    return {session,tabs,local,navigate:details=>navigation(details),alarm:()=>alarm({name:'expire-orders'}),removed,
        message:(msg,sender=localSender)=>new Promise(resolve=>receive(msg,sender,resolve))};
}

test('仅接受明确的本地站点、HTTPS孔夫子来源及完整地址',()=>{
    assert.equal(policy.site('http://localhost:5000/orders'),true);
    assert.equal(policy.site('http://localhost:5001/orders'),false);
    assert.equal(policy.official('https://order.kongfz.com/checkout'),true);
    for(const url of ['http://order.kongfz.com/','https://kongfz.com.evil.invalid/','javascript:alert(1)','https://x@book.kongfz.com/'])
        assert.equal(policy.official(url),false);
    assert.equal(policy.source('http://book.kongfz.com/1/2'),'https://book.kongfz.com/1/2');
    assert.ok(policy.target(target(1)));
    assert.equal(!!policy.target({...target(1),receiver:{...receiver,address:''}}),false);
});

test('不同订单分别绑定新标签页，未关联标签页取不到地址',async()=>{
    const w=worker();
    for(const id of [1,2]) assert.equal((await w.message({type:'OPEN_SOURCE',url:'https://book.kongfz.com/1/2',target:target(id)})).ok,true);
    for(const [tabId,id] of [[10,1],[11,2]]) {
        const result=await w.message({type:'GET_CONTEXT'},{tab:{id:tabId},frameId:0,url:'https://order.kongfz.com/checkout'});
        assert.equal(result.target.order_id,id);
    }
    w.tabs.set(50,{id:50,url:'https://order.kongfz.com/checkout'});
    assert.equal((await w.message({type:'GET_CONTEXT'},{tab:{id:50},frameId:0,url:'https://order.kongfz.com/checkout'})).ok,false);
});

test('购买弹窗沿实际父标签继承本单，不依赖全局最后一单',async()=>{
    const w=worker();
    for(const id of [1,2]) await w.message({type:'OPEN_SOURCE',url:'https://book.kongfz.com/1/2',target:target(id)});
    w.tabs.set(20,{id:20,openerTabId:11,url:'https://order.kongfz.com/checkout'});
    await w.navigate({sourceFrameId:0,sourceTabId:10,tabId:20,url:'https://order.kongfz.com/checkout'});
    const result=await w.message({type:'GET_CONTEXT'},{tab:{id:20},frameId:0,url:'https://order.kongfz.com/checkout'});
    assert.equal(result.target.order_id,1);
});

test('外部网站、子框架及未授权服务器不能创建上下文',async()=>{
    const w=worker();
    const msg={type:'OPEN_SOURCE',url:'https://book.kongfz.com/1/2',target:target(1)};
    for(const sender of [{...localSender,url:'https://evil.invalid/orders'},
        {...localSender,frameId:1},{...localSender,url:'https://my-site.invalid/orders'}])
        assert.equal((await w.message(msg,sender)).ok,false);
    assert.equal(Object.keys(w.session).length,0);
    w.local.siteOrigin='https://my-site.invalid';
    assert.equal((await w.message(msg,{...localSender,url:'https://my-site.invalid/orders'})).ok,true);
});

test('过期信息不能读取，清理时同时释放地址及标签关联',async()=>{
    const w=worker();
    await w.message({type:'OPEN_SOURCE',url:'https://book.kongfz.com/1/2',target:target(1)});
    w.session['order:nonce-1'].expiresAt=Date.now()-1;
    const result=await w.message({type:'GET_CONTEXT'},{tab:{id:10},frameId:0,url:'https://order.kongfz.com/checkout'});
    assert.equal(result.ok,false);
    await w.alarm();
    assert.deepEqual(Object.keys(w.session),[]);
});
