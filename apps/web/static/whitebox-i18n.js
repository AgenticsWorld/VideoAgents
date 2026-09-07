// Translate UI and known compiler diagnostics at display time. Project-authored
// names, scale evidence, IDs and stored artifacts keep their original language.
export function wbText(key, params={}) {
  if(globalThis.I18N)return globalThis.I18N.f(key,params);
  return key.replace(/\{(\w+)\}/g,(match,name)=>params[name]??match);
}

export const diagnosticTemplates = [
  '项目画幅无效：{aspect}',
  '{shot}: 缺少 view_tile，暂取布局的第一个机位，请校准。',
  '{shot}: 沿布局视轴按景别对准镜首主体，机距为推断值。',
  '{shot}: 运镜 {move} 需白模计划提供数值关键帧，暂按固定机位显示。',
  '{shot}: 运镜幅度由 {move} 推断，请校准数值轨迹。',
  '{shot}: 已忽略无明确原因的人物过滤，按实际机位显示在场人物。',
  '{actor}: 同场次缺少空间锚点，请补 scene_actors',
  '{actor}: 同场次在场人物，沿用 {group} 的尾姿态与位置；补充走位可写 scene_actors。',
  '{actor}: 同场次在场人物，沿用 {group} 的首姿态与位置；补充走位可写 scene_actors。',
  '{actor}: 与 {group} 的位置/姿态不连续，请确认切换或继承。',
  '机位与 {group} 不连续。',
];
const patterns=diagnosticTemplates.map(key=>{
  const names=[];
  const pattern=key.split(/(\{\w+\})/).map(part=>{
    if(/^\{\w+\}$/.test(part)){names.push(part.slice(1,-1));return '(.+?)';}
    return part.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
  }).join('');
  return {key,names,regex:new RegExp('^'+pattern+'$')};
});
export function wbMessage(message) {
  for(const {key,names,regex} of patterns){
    const match=String(message).match(regex);
    if(match)return wbText(key,Object.fromEntries(names.map((name,i)=>[name,match[i+1]])));
  }
  return wbText(message);
}
