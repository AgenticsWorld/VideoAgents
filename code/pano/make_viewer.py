"""自包含的全景查看器 HTML(拖动旋转 / 滚轮缩放 / 切换对齐标注图),用浏览器打开即可 360° 查看。

用法: python code/pano/make_viewer.py <pano.png> <annot.jpg|-> <pano.json|-> <out.html>
"""
from __future__ import annotations

import base64
import json
import os
import sys

import cv2


def b64jpg(path: str, q: int = 88) -> str:
    im = cv2.imread(path)
    _, buf = cv2.imencode('.jpg', im, [cv2.IMWRITE_JPEG_QUALITY, q])
    return 'data:image/jpeg;base64,' + base64.b64encode(buf).decode()


def main() -> None:
    pano, annot, meta_p, out = sys.argv[1:5]
    raw = b64jpg(pano)
    ann = b64jpg(annot, 85) if annot != '-' else raw
    meta = json.load(open(meta_p)) if meta_p != '-' else None
    info = (f"观察点 P=({meta['origin_m'][0]},{meta['origin_m'][1]},{meta['origin_m'][2]}) m · 中央列朝向 yaw0={meta['yaw0_deg']}° · "
            f"对齐 rms={meta['alignment']['rms_deg']}° ({meta['alignment']['n_landmarks']} 地标)") if meta else ''
    name = os.path.basename(pano)
    html = f'''<!doctype html><html><head><meta charset="utf-8"><title>{name}</title>
<style>body{{margin:0;background:#111;color:#eee;font:13px/1.4 -apple-system,sans-serif;overflow:hidden}}#hud{{position:fixed;left:10px;top:10px;background:rgba(0,0,0,.55);padding:8px 10px;border-radius:6px}}canvas{{display:block}}</style></head><body>
<div id="hud">{name} · 拖动=旋转 · 滚轮=拉近/拉远 · <button id="t">切换:原图/对齐标注</button><br><span id="s"></span><br>{info}</div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/0.158.0/three.min.js"></script>
<script>
const RAW="{raw}", ANN="{ann}";
const scene=new THREE.Scene(); const cam=new THREE.PerspectiveCamera(60,innerWidth/innerHeight,.1,100);
const r=new THREE.WebGLRenderer({{antialias:true}}); r.setSize(innerWidth,innerHeight); document.body.appendChild(r.domElement);
const geo=new THREE.SphereGeometry(50,96,48); geo.scale(-1,1,1);
const loader=new THREE.TextureLoader(); const texRaw=loader.load(RAW), texAnn=loader.load(ANN); texRaw.colorSpace=texAnn.colorSpace=THREE.SRGBColorSpace;
const mat=new THREE.MeshBasicMaterial({{map:texRaw}}); scene.add(new THREE.Mesh(geo,mat)); let showAnn=false;
document.getElementById('t').onclick=()=>{{showAnn=!showAnn;mat.map=showAnn?texAnn:texRaw;mat.needsUpdate=true}};
let lon=0,lat=0,down=false,px=0,py=0,fov=60;
addEventListener('pointerdown',e=>{{down=true;px=e.clientX;py=e.clientY}}); addEventListener('pointerup',()=>down=false);
addEventListener('pointermove',e=>{{if(!down)return;lon-=(e.clientX-px)*0.1*fov/60;lat+=(e.clientY-py)*0.1*fov/60;lat=Math.max(-85,Math.min(85,lat));px=e.clientX;py=e.clientY}});
addEventListener('wheel',e=>{{fov=Math.max(15,Math.min(100,fov+e.deltaY*0.05));cam.fov=fov;cam.updateProjectionMatrix()}},{{passive:true}});
addEventListener('resize',()=>{{cam.aspect=innerWidth/innerHeight;cam.updateProjectionMatrix();r.setSize(innerWidth,innerHeight)}});
(function loop(){{requestAnimationFrame(loop);const phi=THREE.MathUtils.degToRad(90-lat),theta=THREE.MathUtils.degToRad(lon);
cam.lookAt(Math.sin(phi)*Math.cos(theta),Math.cos(phi),Math.sin(phi)*Math.sin(theta));
document.getElementById('s').textContent='视角 yaw '+((lon%360+360)%360).toFixed(0)+'° pitch '+lat.toFixed(0)+'° fov '+fov.toFixed(0)+'°'; r.render(scene,cam)}})();
</script></body></html>'''
    with open(out, 'w') as f:
        f.write(html)
    print('ok', out, len(html) // 1024, 'KB')


if __name__ == '__main__':
    main()
