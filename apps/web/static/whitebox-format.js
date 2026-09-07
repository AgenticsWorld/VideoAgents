// Resolve the same native frame as modules.whitebox.render_format, including
// decimal custom ratios. The existing config API also works before a restart.
export function projectRenderFormat(settings) {
  const out=settings.output||{};
  const aspect=out.aspect_preset==='custom'
    ? ((out.aspect_custom||'').replace(/\s/g,'')||'16:9')
    : out.aspect_preset==='douyin'?'9:16':'16:9';
  if(!/^\d+(?:\.\d+)?:\d+(?:\.\d+)?$/.test(aspect))throw Error('项目画幅无效：'+aspect);
  const fraction=s=>[Number(s.replace('.','')),10**(s.split('.')[1]?.length||0)];
  const [x,y]=aspect.split(':').map(fraction);
  let a=x[0]*y[1],b=y[0]*x[1];
  if(!Number.isSafeInteger(a)||!Number.isSafeInteger(b)||a<=0||b<=0)throw Error('项目画幅无效：'+aspect);
  const gcd=(a,b)=>b?gcd(b,a%b):a;
  const div=gcd(a,b);a/=div;b/=div;
  const scale=Math.max(1,Math.floor(960/(2*Math.max(a,b))));
  const width=2*a*scale,height=2*b*scale;
  if(Math.min(width,height)<128||Math.max(width,height)>1920)throw Error('项目画幅超出白模参考视频尺寸范围');
  return {aspect_ratio:aspect,width,height};
}
