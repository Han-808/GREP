import * as THREE from 'three';
import { GLTFLoader } from './vendor/addons/loaders/GLTFLoader.js';
import { HDRLoader } from './vendor/addons/loaders/HDRLoader.js';
import { RoundedBoxGeometry } from './vendor/RoundedBoxGeometry.js';
import { doorClearance, doorSwingLimit } from './cabinet-door-geometry.js?v=dff24e64116f';

/** Real glTF furniture, local PBR textures, and the original room layout. */
export async function buildRealisticRoom(scene, renderer, onProgress) {
  const asset = path => new URL(path, import.meta.url).href;
  const gltf = new GLTFLoader();
  const files = {
    sofa:'models/sofa.glb',
    chair:'models/modern_arm_chair_01/modern_arm_chair_01.gltf',
    table:'models/coffee_table_round_01/coffee_table_round_01.gltf',
    cabinet:'models/modern_wooden_cabinet/modern_wooden_cabinet.gltf',
    side:'models/side_table_01/side_table_01.gltf',
    lamp:'models/desk_lamp_arm_01/desk_lamp_arm_01.gltf',
    shelf:'models/wooden_bookshelf_worn/wooden_bookshelf_worn.gltf',
  };
  let completed = 0;
  const loaded = Object.fromEntries(await Promise.all(Object.entries(files).map(async([name,path]) => {
    const value = await gltf.loadAsync(asset(path));
    onProgress?.(++completed,Object.keys(files).length);
    return [name,value.scene];
  })));
  const textureLoader = new THREE.TextureLoader();
  const [walnutColor,walnutNormal,walnutRoughness,rugMap,environment,linen,screenMap] = await Promise.all([
    textureLoader.loadAsync(asset('textures/walnut-clean-color.jpg')),
    textureLoader.loadAsync(asset('textures/walnut-clean-normal.jpg')),
    textureLoader.loadAsync(asset('textures/walnut-clean-roughness.jpg')),
    textureLoader.loadAsync(asset('textures/rug.png')),
    new HDRLoader().loadAsync(asset('lighting/studio_small_09.hdr')),
    textureLoader.loadAsync(asset('textures/upholstery.png')),
    textureLoader.loadAsync(asset('textures/screen.png')),
  ]);
  walnutColor.colorSpace=THREE.SRGBColorSpace;rugMap.colorSpace=THREE.SRGBColorSpace;
  for(const t of [walnutColor,walnutNormal,walnutRoughness]){t.wrapS=t.wrapT=THREE.RepeatWrapping;t.repeat.set(1,1);t.anisotropy=8;}
  rugMap.anisotropy=8;
  linen.colorSpace=THREE.SRGBColorSpace;linen.wrapS=linen.wrapT=THREE.RepeatWrapping;linen.repeat.set(4,4);linen.anisotropy=8;
  screenMap.colorSpace=THREE.SRGBColorSpace;
  const pmrem=new THREE.PMREMGenerator(renderer);scene.environment=pmrem.fromEquirectangular(environment).texture;scene.environmentIntensity=.62;environment.dispose();pmrem.dispose();
  const wood=new THREE.MeshStandardMaterial({map:walnutColor,normalMap:walnutNormal,roughnessMap:walnutRoughness,normalScale:new THREE.Vector2(.22,.22),roughness:.68,color:'#d2ab82'});
  const darkWood=wood.clone();darkWood.color=new THREE.Color('#93867b');
  const steel=new THREE.MeshStandardMaterial({color:'#30312f',metalness:.8,roughness:.3});
  const bronze=new THREE.MeshStandardMaterial({color:'#94724e',metalness:.7,roughness:.36});
  function material(color,roughness=.8){return new THREE.MeshStandardMaterial({color,roughness});}
  const ceramic=material('#d8d3c7',.29), pageMat=material('#c6bcaa'), cloth=material('#8c8174');
  const baseGroup=()=>{const g=new THREE.Group();scene.add(g);return g;};
  function mesh(parent,geo,mat,x=0,y=0,z=0){const m=new THREE.Mesh(geo,typeof mat==='string'?material(mat):mat);m.position.set(x,y,z);m.castShadow=true;m.receiveShadow=true;parent.add(m);return m;}
  function box(parent,w,h,d,x,y,z,mat,r=.008){return mesh(parent,new RoundedBoxGeometry(w,h,d,2,Math.min(r,w/4,h/4,d/4)),mat,x,y,z);}
  function cylinder(parent,rt,rb,h,x,y,z,mat,n=32){return mesh(parent,new THREE.CylinderGeometry(rt,rb,h,n),mat,x,y,z);}
  function object(name,x,z,size,axis='x',rotation=0,y=.045){
    const container=baseGroup();container.position.set(x,y,z);container.rotation.y=rotation;
    const model=loaded[name].clone(true);model.updateMatrixWorld(true);
    const initial=new THREE.Box3().setFromObject(model),dimensions=initial.getSize(new THREE.Vector3());
    model.scale.multiplyScalar(size/dimensions[axis]);model.updateMatrixWorld(true);
    const bounds=new THREE.Box3().setFromObject(model),center=bounds.getCenter(new THREE.Vector3());
    model.position.add(new THREE.Vector3(-center.x,-bounds.min.y,-center.z));
    model.traverse(child=>{if(child.isMesh){child.castShadow=true;child.receiveShadow=true;for(const mat of Array.isArray(child.material)?child.material:[child.material]){if(mat.map)mat.map.anisotropy=8;}}});
    container.add(model);container.name=name;return container;
  }
  // Neutral benchmark floor: no enclosing walls, vegetation, or raised wooden plinth.
  box(scene,7.2,.055,5.8,0,-.025,0,'#b9babc',.009);
  const rugMat=new THREE.MeshStandardMaterial({map:rugMap,roughness:1,color:'#d8cabb'});
  box(scene,3.95,.014,3.3,-.66,.016,-.2,rugMat,.003);
  const rug2=rugMat.clone();rug2.color=new THREE.Color('#9b8e83');
  box(scene,1.93,.012,1.88,2.17,.013,-1.75,rug2,.003);

  const sofa=object('sofa',-.97,-1.82,2.95,'x',0);
  sofa.traverse(part=>{
    if(!part.isMesh)return;
    const previous=part.material;
    if(previous.name!=='Frame'&&previous.name!=='Fringe'){
      const textile=linen.clone();textile.needsUpdate=true;const repeat=previous.name==='Frame_Fabric'?5:3;textile.repeat.set(repeat,repeat);
      part.material=new THREE.MeshPhysicalMaterial({map:textile,roughness:.95,bumpMap:textile,bumpScale:.002,sheen:.45,sheenColor:new THREE.Color('#d4cabc'),sheenRoughness:.85,color:previous.name==='Brown'?'#b0b5a4':'#eee7db',aoMap:previous.aoMap,aoMapIntensity:.55});
    }else if(previous.name==='Frame'){part.material=wood.clone();part.material.color=new THREE.Color('#c5a27e');}
    else if(previous.name==='Fringe'){part.visible=false;}
  });
  const chair=object('chair',1.08,.22,.89,'x',-.18);chair.userData.finding='collision';
  function softenChair(target,color){target.traverse(part=>{if(part.isMesh&&part.material.name.includes('pillow')){const old=part.material;part.material=new THREE.MeshPhysicalMaterial({map:linen,normalMap:old.normalMap,normalScale:new THREE.Vector2(.55,.55),aoMap:old.aoMap,roughness:.88,bumpMap:linen,bumpScale:.0015,sheen:.28,sheenColor:new THREE.Color(color),sheenRoughness:.8,color});}});}
  softenChair(chair,'#7c8878');
  const table=object('table',.15,.25,1.35,'x');table.userData.finding='collision';
  const shelf=object('shelf',-2.98,-1.65,2.12,'y',Math.PI/2);
  shelf.traverse(part=>{if(part.isMesh){const old=part.material;part.material=wood.clone();part.material.aoMap=old.aoMap;part.material.aoMapIntensity=.8;part.material.color=new THREE.Color('#b4a08b');}});
  // A working cabinet: the single door has a real hinge, visible interior, and
  // a finite opening sweep. Its front faces the room (world -x).
  const cabinet=baseGroup();cabinet.position.set(2.97,0,1.55);cabinet.rotation.y=-Math.PI/2;
  cabinet.name='Hinged storage cabinet';cabinet.userData.finding='access';
  const cabinetTop=1.12;
  box(cabinet,1.12,.045,.51,0,cabinetTop-.0225,0,wood,.004);
  box(cabinet,1.12,.045,.51,0,.145,0,wood,.004);
  for(const x of [-.54,.54])box(cabinet,.04,.93,.50,x,.625,0,wood,.003);
  box(cabinet,1.04,.93,.025,0,.625,-.235,darkWood,.002);
  for(const y of [.46,.78])box(cabinet,1.04,.025,.45,0,y,-.018,wood,.002);
  for(const x of [-.46,.46])for(const z of [-.17,.17]){
    cylinder(cabinet,.024,.021,.13,x,.068,z,steel,16);
  }
  const doorHinge=new THREE.Group();cabinet.add(doorHinge);doorHinge.position.set(-.515,0,.265);
  const doorWidth=1.03,doorThickness=.035;
  // The slab has flat sides so the collision section matches the visible panel.
  mesh(doorHinge,new THREE.BoxGeometry(doorWidth,.92,doorThickness),wood,doorWidth/2,.625,0);
  const insetWood=wood.clone();insetWood.color=new THREE.Color('#bba184');
  box(doorHinge,.91,.80,.008,doorWidth/2,.625,.0205,insetWood,.003);
  for(const y of [.28,.96]){
    cylinder(cabinet,.014,.014,.09,-.515,y,.265,bronze,20);
    box(doorHinge,.045,.075,.004,.025,y,.020,bronze,.002);
  }
  for(const y of [.55,.70])box(doorHinge,.022,.016,.040,.91,y,.040,bronze,.003);
  box(doorHinge,.024,.184,.026,.91,.625,.067,bronze,.005);
  const tvConsole=object('cabinet',-2.97,1.03,2.06,'x',Math.PI/2);
  const tvGroup=baseGroup();tvGroup.position.set(-2.97,0,1.03);tvGroup.rotation.y=Math.PI/2;
  box(tvGroup,1.66,.95,.045,0,1.13,-.03,steel,.016);
  box(tvGroup,1.59,.88,.006,0,1.13,-.003,new THREE.MeshPhysicalMaterial({map:screenMap,color:'#767b7e',metalness:.12,roughness:.12,clearcoat:1,clearcoatRoughness:.025,envMapIntensity:.6}),.005);
  box(tvGroup,.13,.012,.006,0,.682,.003,'#363b3f',.002);
  const powerLight=new THREE.Mesh(new THREE.SphereGeometry(.003,8,6),new THREE.MeshBasicMaterial({color:'#8baaba'}));powerLight.position.set(.73,.68,.008);tvGroup.add(powerLight);
  for(const x of [-.55,.55]){const foot=box(tvGroup,.027,.14,.22,x,.63,0,steel,.004);foot.rotation.z=x>0?-.2:.2;}
  const desk=baseGroup();desk.position.set(2.2,0,-1.91);
  box(desk,1.82,.065,.84,0,.795,0,wood,.012);
  for(const x of [-.82,.82])for(const z of [-.32,.32])box(desk,.036,.76,.036,x,.4,z,steel,.003);
  box(desk,.035,.055,.7,-.82,.16,0,steel,.003);box(desk,.035,.055,.7,.82,.16,0,steel,.003);
  box(desk,.6,.2,.66,.55,.66,0,darkWood,.008);box(desk,.51,.12,.015,.55,.66,.34,wood,.004);box(desk,.14,.014,.028,.55,.69,.36,bronze,.003);
  const deskChair=object('chair',2.2,-1.03,.68,'x',Math.PI);softenChair(deskChair,'#c1b8a8');
  const laptopMat=new THREE.MeshStandardMaterial({color:'#454a4d',metalness:.85,roughness:.32});
  box(desk,.56,.025,.36,.24,.842,.02,laptopMat,.009);
  box(desk,.49,.004,.19,.24,.858,.01,'#292e30',.004);
  const screen=box(desk,.56,.35,.02,.24,1.015,-.16,laptopMat,.008);screen.rotation.x=-.15;
  const display=box(desk,.515,.305,.004,.24,1.015,-.145,'#43525c',.005);display.rotation.x=-.15;
  const lamp=object('lamp',1.65,-2.05,.62,'y',0,.84);lamp.userData.finding='scale';lamp.scale.setScalar(2.65);
  const side=object('side',-2.6,-.1,.58,'y');

  function book(parent,x,y,z,w=.3,d=.22,h=.035,color='#4b5453',angle=0){
    const b=new THREE.Group();parent.add(b);b.position.set(x,y,z);b.rotation.y=angle;
    box(b,w,h,d,0,h/2,0,color,.003);box(b,w-.014,h-.009,d-.013,.004,h/2,.003,pageMat,.001);
    box(b,w+.004,.004,d+.004,0,h+.002,0,color,.001);box(b,.012,h,d,-w/2+.003,h/2,0,color,.002);
    return b;
  }
  function cup(parent,x,y,z,size=1){
    const c=new THREE.Group();c.position.set(x,y,z);c.scale.setScalar(size);parent.add(c);
    const points=[new THREE.Vector2(.035,0),new THREE.Vector2(.046,.008),new THREE.Vector2(.05,.09),new THREE.Vector2(.046,.095),new THREE.Vector2(.043,.09),new THREE.Vector2(.035,.012)];
    mesh(c,new THREE.LatheGeometry(points,32),ceramic);const handle=mesh(c,new THREE.TorusGeometry(.026,.006,10,24),ceramic,.057,.055,0);handle.rotation.y=Math.PI/2;
    cylinder(c,.039,.039,.002,0,.076,0,'#34251d');cylinder(c,.085,.082,.008,0,-.003,0,ceramic);
  }
  function vase(parent,x,y,z,height=.25){
    const points=[[.07,0],[.10,.025],[.12,.1],[.10,.17],[.047,.22],[.045,.25]].map(([r,h])=>new THREE.Vector2(r,h));
    const v=mesh(parent,new THREE.LatheGeometry(points,40),new THREE.MeshStandardMaterial({color:'#756c59',roughness:.38}),x,y,z);v.scale.setScalar(height/.25);return v;
  }
  // A restrained set of real-world props adds density without foliage.
  book(table,-.15,.515,.12,.34,.25,.04,'#475550',.12);book(table,-.14,.56,.12,.3,.22,.03,'#80674c',.05);
  cup(table,.26,.52,.08,.95);vase(table,.07,.52,-.24,.22);
  book(desk,.63,.83,.18,.24,.18,.025,'#344d58',-.12);cup(desk,-.58,.83,.23,.9);
  cylinder(desk,.045,.04,.11,.66,.89,-.27,'#85735a');
  for(let i=0;i<5;i++){const pen=cylinder(desk,.003,.003,.18,.66+(i%3-.8)*.012,1.01,-.27+(i%2)*.012,i%2?'#282b2b':'#a4875e',8);pen.rotation.z=(i-2)*.06;}
  cup(side,0,.59,0,1);book(side,-.08,.58,.02,.22,.17,.025,'#57483a',.2);
  book(cabinet,-.24,cabinetTop,.02,.3,.23,.04,'#5c655b');book(cabinet,-.22,cabinetTop+.045,.02,.29,.22,.027,'#b7a58c',.15);
  vase(cabinet,.32,cabinetTop,0,.22);
  // Stored objects make the cabinet's intended use readable once the door opens.
  book(cabinet,-.22,.474,-.035,.34,.25,.045,'#675343');
  book(cabinet,-.20,.523,-.035,.31,.23,.035,'#a49781',.04);
  box(cabinet,.34,.20,.30,.25,.27,-.04,cloth,.012);
  box(cabinet,.33,.035,.29,.25,.383,-.04,'#afa08c',.008);
  cup(cabinet,.24,.796,-.05,.85);
  vase(cabinet,-.23,.794,-.06,.24);
  const coverColors=['#343e40','#765344','#858573','#b6a78a','#4f5d5b','#8d7860','#c8b9a0'];
  for(let row=0;row<4;row++){
    const y=.12+row*.49;
    for(let i=0;i<8;i++){
      const height=.25+((i*7+row*3)%5)*.025;
      const b=box(shelf,.056,height,.2,-.48+i*.09,y+height/2,.055,coverColors[(i+row)%coverColors.length],.002);
      if(i===7)b.rotation.z=-.12;
      box(shelf,.037,.012,.003,-.48+i*.09,y+height*.68,.158,'#c5baa5',.001);
    }
    if(row%2===0)vase(shelf,.43,y,.03,.21);
    else{book(shelf,.4,y,.02,.27,.2,.035,'#695646');book(shelf,.39,y+.04,.02,.24,.19,.028,'#586065',.04);}
  }
  const ottoman=baseGroup();ottoman.position.set(2.10,0,1.64);ottoman.userData.finding='access';
  const poufMat=new THREE.MeshStandardMaterial({map:linen,color:'#aa9b89',roughness:.95,bumpMap:linen,bumpScale:.002});
  const pouf=mesh(ottoman,new THREE.SphereGeometry(1,48,24),poufMat,0,.31,0);pouf.scale.set(.43,.32,.43);
  for(let i=0;i<8;i++){
    const a=i*Math.PI/4,pts=[];for(let j=0;j<25;j++){const t=j/24*Math.PI;pts.push(new THREE.Vector3(.432*Math.sin(t)*Math.cos(a),.31+.322*Math.cos(t),.432*Math.sin(t)*Math.sin(a)));}
    ottoman.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts),new THREE.LineBasicMaterial({color:'#c1a991',transparent:true,opacity:.5})));
  }
  // The intended sweep and actual opening share the same hinge and door width.
  const doorGuide=new THREE.Group();cabinet.add(doorGuide);doorGuide.visible=false;
  const guidePoints=[];
  for(let i=0;i<=48;i++){
    const angle=i/48*Math.PI/2;
    guidePoints.push(new THREE.Vector3(-.515+doorWidth*Math.cos(angle),.032,.265+doorWidth*Math.sin(angle)));
  }
  const intendedArc=new THREE.Line(new THREE.BufferGeometry().setFromPoints(guidePoints),new THREE.LineDashedMaterial({color:'#718d90',dashSize:.055,gapSize:.035,transparent:true,opacity:.75}));
  intendedArc.computeLineDistances();doorGuide.add(intendedArc);
  const currentArc=new THREE.Line(new THREE.BufferGeometry().setFromPoints(guidePoints),new THREE.LineBasicMaterial({color:'#087f86',transparent:true,opacity:.95}));doorGuide.add(currentArc);
  const limitMarker=mesh(doorGuide,new THREE.SphereGeometry(.028,16,12),new THREE.MeshBasicMaterial({color:'#bc6448'}));
  let requestedProgress=0;
  let mechanismState={};
  const localObstacle=new THREE.Vector3(),obstacleScale=new THREE.Vector3();
  const cabinetMechanism={
    setOpenProgress(progress){
      requestedProgress=THREE.MathUtils.clamp(Number(progress)||0,0,1);
      cabinet.updateWorldMatrix(true,true);ottoman.updateWorldMatrix(true,true);
      pouf.getWorldPosition(localObstacle);cabinet.worldToLocal(localObstacle);
      pouf.getWorldScale(obstacleScale);
      // Each moving part intersects a different horizontal section of the pouf.
      // The handle must not collide with an imaginary full-height cylinder.
      const part=(minX,maxX,minZ,maxZ,minY,maxY)=>{
        const yDistance=Math.max(minY-localObstacle.y,localObstacle.y-maxY,0);
        const section=Math.sqrt(Math.max(0,1-(yDistance/obstacleScale.y)**2));
        return {minX,maxX,minZ,maxZ,radius:section>0?Math.max(obstacleScale.x,obstacleScale.z)*section:null};
      };
      const spec={hinge:{x:-.515,z:.265},obstacle:{x:localObstacle.x,z:localObstacle.z,radius:.43},parts:[
        part(0,doorWidth,-doorThickness/2,doorThickness/2,.165,1.085),
        part(.06,.97,.0165,.0245,.225,1.025),
        part(.898,.922,.054,.080,.533,.717),
        part(.899,.921,.020,.060,.542,.558),
        part(.899,.921,.020,.060,.692,.708),
      ]};
      const maxAngle=doorSwingLimit(spec);
      const requestedAngle=requestedProgress*Math.PI/2,angle=Math.min(requestedAngle,maxAngle);
      doorHinge.rotation.y=-angle;
      const contact=doorClearance(angle,spec);
      const blocked=requestedAngle>maxAngle+1e-5;
      const arcPositions=currentArc.geometry.attributes.position;
      for(let i=0;i<=48;i++){
        const t=i/48*angle;
        arcPositions.setXYZ(i,-.515+doorWidth*Math.cos(t),.034,.265+doorWidth*Math.sin(t));
      }
      arcPositions.needsUpdate=true;currentArc.geometry.computeBoundingSphere();
      currentArc.material.color.set(blocked?'#bc6448':'#087f86');
      limitMarker.position.set(contact.x,.036,contact.z);limitMarker.visible=blocked;
      mechanismState={requestedAngleDeg:THREE.MathUtils.radToDeg(requestedAngle),angleDeg:THREE.MathUtils.radToDeg(angle),maxAngleDeg:THREE.MathUtils.radToDeg(maxAngle),blocked,clearance:contact.clearance,closedClearance:doorClearance(0,spec).clearance};
      return {...mechanismState};
    },
    getState(){return this.setOpenProgress(requestedProgress);},
    setGuideVisible(visible){doorGuide.visible=Boolean(visible);},
  };
  cabinetMechanism.setOpenProgress(0);
  const bench=baseGroup();bench.position.set(.05,0,2.25);
  box(bench,1.28,.07,.45,0,.34,0,wood,.008);for(const x of [-.52,.52])for(const z of [-.16,.16])box(bench,.025,.3,.025,x,.17,z,steel,.002);
  book(bench,-.32,.38,0,.29,.22,.03,'#4a5756',.08);book(bench,-.3,.415,0,.26,.2,.025,'#b8a386',.03);
  // A camera proxy and frustum make viewpoint acquisition visible, as in the paper.
  const rig=baseGroup();rig.name='GREP evidence camera';rig.position.set(-.7,1.3,2.1);
  box(rig,.2,.13,.09,0,0,0,steel,.015);cylinder(rig,.044,.05,.075,0,0,.075,steel,24).rotation.x=Math.PI/2;
  cylinder(rig,.032,.032,.008,0,0,.115,new THREE.MeshStandardMaterial({color:'#376674',metalness:.4,roughness:.12})).rotation.x=Math.PI/2;
  const corners=[[-.35,-.2,1], [.35,-.2,1],[.35,.2,1],[-.35,.2,1]],lines=[];
  for(let i=0;i<4;i++){lines.push(0,0,.13,...corners[i],...corners[i],...corners[(i+1)%4]);}
  const frustumGeo=new THREE.BufferGeometry();frustumGeo.setAttribute('position',new THREE.Float32BufferAttribute(lines,3));
  rig.add(new THREE.LineSegments(frustumGeo,new THREE.LineBasicMaterial({color:'#78dce6',transparent:true,opacity:.92,depthWrite:false})));
  const frustumFaces=[];for(let i=0;i<4;i++)frustumFaces.push(0,0,.13,...corners[i],...corners[(i+1)%4]);
  const coneGeometry=new THREE.BufferGeometry();coneGeometry.setAttribute('position',new THREE.Float32BufferAttribute(frustumFaces,3));
  rig.add(new THREE.Mesh(coneGeometry,new THREE.MeshBasicMaterial({color:'#65d3df',transparent:true,opacity:.08,side:THREE.DoubleSide,depthWrite:false})));
  const pathPoints=[new THREE.Vector3(-3.35,.06,2.55),new THREE.Vector3(-1.8,.06,2.8),new THREE.Vector3(0,.06,2.78),new THREE.Vector3(1.9,.06,2.65),new THREE.Vector3(3.4,.06,1.8),new THREE.Vector3(3.4,.06,.5)];
  const route=new THREE.Group();scene.add(route);
  const curve=new THREE.CatmullRomCurve3(pathPoints),samples=curve.getSpacedPoints(100);
  const dashGeometry=new THREE.CylinderGeometry(.013,.013,1,8);
  const dashMaterial=new THREE.MeshBasicMaterial({color:'#4ab9c5',transparent:true,opacity:.85});
  const dashes=new THREE.InstancedMesh(dashGeometry,dashMaterial,50);
  for(let i=0;i<50;i++){const a=samples[i*2],b=samples[i*2+1],q=new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0,1,0),b.clone().sub(a).normalize());dashes.setMatrixAt(i,new THREE.Matrix4().compose(a.clone().add(b).multiplyScalar(.5),q,new THREE.Vector3(1,a.distanceTo(b),1)));}
  route.add(dashes);
  return {chair,table,lamp,cabinet,ottoman,rig,route,cabinetMechanism};
}
