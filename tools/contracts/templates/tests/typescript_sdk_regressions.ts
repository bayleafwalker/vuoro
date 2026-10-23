/** Root-owned actual Hey API regressions. No product instrumentation API. */
import assert from 'node:assert/strict';
import * as attachment from '__ATTACHMENT__';
import * as sdk from '__SDK__';
import {createClient} from '__CLIENT__';
import {ContractError} from '__DECODER__';
const scenario = process.argv[2];
const body = {revision:'12',enabled:true,nested:{label:'x'}};
const success = {state:'ready',revision:'12'};
const wires:any[]=[];
(globalThis as any).fetch=async()=>{throw new Error('regression_network_forbidden')};
const fetcher:typeof fetch=async(input:any)=>{
 assert(input instanceof Request);wires.push({method:input.method,url:input.url,header:input.headers.get('x-token')});
 return new Response(JSON.stringify(success),{status:200,headers:{'Content-Type':'application/json'}});
};
const opts:any={path:{key:'k'},body};
if(scenario==='header'||scenario==='serialized_header')opts.headers={'x-token':'k'};
// Direct real native call is the independent variant positive, before attachment assertions.
const nativeOperation=scenario==='underscore_operation'?(sdk as any).submitItem:sdk.submit;
assert.equal(typeof nativeOperation,'function','actual_renamed_native_export_missing');
const native=await nativeOperation({...opts,client:createClient({baseUrl:'https://contracts.invalid',fetch:fetcher}),throwOnError:true});
assert.deepEqual(native.data,success);assert.equal(wires.length,1);
const client=attachment.createValidatedClient({baseUrl:'https://contracts.invalid',fetch:fetcher});
const operation=scenario==='underscore_operation'?'submit_item':'submit';
assert.deepEqual((await client[operation](opts)).body,success);
assert.equal(wires.length,2,'guard_did_not_use_transport');
assert.equal((globalThis as any).__regressionNative,2,'guard_did_not_execute_actual_native_sdk');
assert.equal((globalThis as any).__regressionSerializers,2,'guard_did_not_execute_actual_native_serializer');
if(opts.headers)assert.equal(wires.at(-1).header,'k');
async function refuse(options:any,method='submit'){
 const before=wires.length;const serializers=(globalThis as any).__regressionSerializers;
 await assert.rejects(()=>client[method](options),(e:any)=>{
  assert(e instanceof ContractError,'unsafe_native_error');
  assert(!JSON.stringify({message:e.message,...e,stack:e.stack}).includes('PRIVATE_'),'caller_data_leaked_from_error');return true;
 });
 assert.equal(wires.length,before,'invalid_request_reached_transport');
 if(scenario==='unsupported_groups')assert.equal((globalThis as any).__regressionSerializers,serializers,'unsupported_option_reached_serializer');
}
if(scenario.startsWith('serialized_')){
 (globalThis as any).__regressionMutation=scenario;
 const request={...opts};if(scenario==='serialized_query')request.query={selector:'k'};
 await refuse(request);
}else if(scenario==='unsupported_groups'){
 for(const invalid of [
  {...opts,query:{PRIVATE_QUERY:'PRIVATE_VALUE'}},
  {...opts,headers:{PRIVATE_HEADER:'PRIVATE_VALUE'}},
  {...opts,path:{key:'k',PRIVATE_PATH:'PRIVATE_VALUE'}},
  {...opts,header:{PRIVATE_HEADER:'PRIVATE_VALUE'}},
  {...opts,_name:'PRIVATE_OPERATION'},
  {...opts,fetch:fetcher},
 ])await refuse(invalid);
}else if(scenario!=='header'&&scenario!=='tagged'&&scenario!=='underscore_operation')throw new Error('unknown_regression_scenario');
console.log(JSON.stringify({scenario,result:'pass'}));
