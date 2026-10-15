import * as THREE from 'three';
import { OrbitControls } from './vendor/OrbitControls.js';
import { buildRealisticRoom } from './realistic-room.js';

const $ = (id) => document.getElementById(id);
const stage = $('scene-stage');
const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;
const colors = { collision: '#bc5e52', scale: '#a87824', access: '#267b72' };
const findings = {
  collision: { number: '01', category: 'Physical validity', title: 'Two objects, one occupied space.',
    description: 'The coffee table intersects the armchair. Inspect the highlighted contact area, then compare the adjusted arrangement.',
    adjustedTitle: 'Give each object its own space.', adjustedDescription: 'The coffee table has moved away from the armchair. The two surfaces no longer occupy the same space in this demonstration.',
    adjustedLabel: 'Objects separated', originalLabel: 'Object collision' },
  scale: { number: '02', category: 'Visual coherence', title: 'A desk lamp at the wrong scale.',
    description: 'The lamp is disproportionately large beside the desk and chair. Familiar nearby objects provide the scale reference.',
    adjustedTitle: 'Bring the lamp back into proportion.', adjustedDescription: 'The same lamp is resized while the desk, chair, and other objects stay fixed. Compare its proportions against the surrounding furniture.',
    adjustedLabel: 'Lamp resized', originalLabel: 'Scale mismatch' },
  access: { number: '03', category: 'Human-use logic', title: 'Furniture needs room to be used.',
    description: 'The ottoman occupies the approach area in front of the cabinet. An object can fit in the room while obstructing another object’s use.',
    adjustedTitle: 'Keep the approach area clear.', adjustedDescription: 'The ottoman has moved out of the cabinet’s approach area. The highlighted space is now open in this illustrative arrangement.',
    adjustedLabel: 'Access cleared', originalLabel: 'Blocked access' },
};
let selected = 'collision';
let adjusted = false;
let annotations = true;
let sceneAPI = null;

function updateCopy() {
  const finding = findings[selected];
  for (const button of document.querySelectorAll('[data-finding]')) {
    const active = button.dataset.finding === selected;
    button.classList.toggle('selected', active);
    button.setAttribute('aria-pressed', String(active));
    if (button.classList.contains('finding-row')) button.querySelector('strong').textContent = adjusted ? findings[button.dataset.finding].adjustedLabel : findings[button.dataset.finding].originalLabel;
  }
  $('detail-category').textContent = finding.category;
  $('detail-category').style.color = colors[selected];
  $('detail-title').textContent = adjusted ? finding.adjustedTitle : finding.title;
  $('detail-description').textContent = adjusted ? finding.adjustedDescription : finding.description;
  $('detail-status').textContent = adjusted ? 'Adjusted example' : 'Illustrative issue';
  $('detail-status').classList.toggle('resolved', adjusted);
  $('evidence-label').textContent = `${finding.number} / CLOSE-UP`;
  $('evidence-canvas').setAttribute('aria-label', `Close-up: ${adjusted ? finding.adjustedLabel : finding.originalLabel}`);
  $('finding-count').textContent = adjusted ? '3 illustrative changes' : '3 illustrative findings';
  $('scene-version-label').textContent = adjusted ? 'Adjusted arrangement' : 'Original arrangement';
  for (const mode of ['original', 'adjusted']) {
    const active = (mode === 'adjusted') === adjusted;
    $(`${mode}-btn`).classList.toggle('selected', active);
    $(`${mode}-btn`).setAttribute('aria-pressed', String(active));
  }
}
function selectFinding(id, focus = false, fromTour = false) {
  selected = id; updateCopy(); sceneAPI?.select(id, focus, fromTour);
}
document.querySelectorAll('[data-finding]').forEach(button => button.addEventListener('click', () => selectFinding(button.dataset.finding)));
for (const mode of ['original', 'adjusted']) $(`${mode}-btn`).addEventListener('click', () => {
  adjusted = mode === 'adjusted'; updateCopy(); sceneAPI?.setAdjusted(adjusted);
});
$('focus-btn').addEventListener('click', () => sceneAPI?.acquireView());
$('tour-btn').addEventListener('click', () => sceneAPI?.toggleTour());
$('overview-btn').addEventListener('click', () => sceneAPI?.view('perspective'));
$('top-btn').addEventListener('click', () => sceneAPI?.view('top'));
$('reset-btn').addEventListener('click', () => sceneAPI?.view('perspective'));
$('annotations-btn').addEventListener('click', () => {
  annotations = !annotations;
  $('annotations-btn').setAttribute('aria-pressed', String(annotations));
  $('hotspots').hidden = !annotations; sceneAPI?.setAnnotations(annotations);
  $('camera-label').hidden = !annotations;
});

function failGracefully(error) {
  console.error('GREP scene preview:', error);
  $('loading-state').hidden = true; $('webgl-fallback').hidden = false;
  $('hotspots').hidden = true; $('room-canvas').hidden = true;
  stage.dataset.ready = 'unavailable';
  for (const id of ['focus-btn','overview-btn','top-btn','reset-btn','annotations-btn']) $(id).disabled = true;
}

createRoom().then(api => { sceneAPI = api; updateCopy(); }).catch(failGracefully);

async function createRoom() {
  const canvas = $('room-canvas');
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, powerPreference: 'low-power' });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 1.8));
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFShadowMap;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = .95;
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  const scene = new THREE.Scene();
  scene.background = new THREE.Color('#363739');
  const camera = new THREE.OrthographicCamera(-6, 6, 3.6, -3.6, .05, 100);
  camera.position.set(8.5, 9.5, 10);
  camera.zoom=1.06;
  const controls = new OrbitControls(camera, canvas);
  controls.target.set(0, .45, 0);
  controls.enableDamping = true; controls.dampingFactor = .12;
  controls.enablePan = false; controls.minZoom = .65; controls.maxZoom = 2.7;
  controls.minPolarAngle = .025; controls.maxPolarAngle = Math.PI / 2 - .13;
  controls.rotateSpeed = .65; controls.zoomSpeed = .7;
  controls.update();
  scene.add(new THREE.HemisphereLight('#ffffff', '#a2a1a0', .55));
  const sun = new THREE.DirectionalLight('#fff6e8', 2.5);
  sun.position.set(-3, 10, 5); sun.castShadow = true;
  sun.shadow.mapSize.set(2048, 2048);
  Object.assign(sun.shadow.camera, { left: -6, right: 6, top: 6, bottom: -6, near: .5, far: 25 });
  sun.shadow.normalBias = .025; sun.shadow.bias = -.00015;
  scene.add(sun);
  const fill = new THREE.DirectionalLight('#e5edff', .35); fill.position.set(5, 5, -4); scene.add(fill);
  const ground = new THREE.Mesh(new THREE.PlaneGeometry(200,200), new THREE.ShadowMaterial({ opacity: .12 }));
  ground.rotation.x = -Math.PI / 2; ground.position.y = -.2; ground.receiveShadow = true; scene.add(ground);

  function mesh(parent, geometry, material, x=0, y=0, z=0) {
    const object=new THREE.Mesh(geometry,material);object.position.set(x,y,z);parent.add(object);return object;
  }
  const {chair,table,lamp,cabinet,ottoman,rig,route}=await buildRealisticRoom(scene,renderer,(count,total)=>{
    $('loading-state').querySelector('strong').textContent=`Loading the room · ${count}/${total}`;
  });

  const highlights=new THREE.Group();scene.add(highlights);
  const helpers={};
  function volume(id,min,max) {
    const b=new THREE.Box3(new THREE.Vector3(...min),new THREE.Vector3(...max));
    const wire=new THREE.Box3Helper(b,new THREE.Color(colors[id]));wire.material.transparent=true;wire.material.opacity=.8;wire.material.depthTest=false;wire.renderOrder=4;highlights.add(wire);return wire;
  }
  helpers.collision=volume('collision',[.48,.39,-.1],[.91,.72,.57]);
  helpers.scale=new THREE.BoxHelper(lamp,new THREE.Color(colors.scale));helpers.scale.material.transparent=true;helpers.scale.material.opacity=.7;helpers.scale.renderOrder=4;highlights.add(helpers.scale);
  helpers.access=volume('access',[1.48,.07,.8],[2.65,.1,2.34]);
  const accessWash=mesh(highlights,new THREE.PlaneGeometry(1.17,1.54),new THREE.MeshBasicMaterial({color:colors.access,transparent:true,opacity:.11,depthWrite:false}),2.065,.066,1.57);accessWash.rotation.x=-Math.PI/2;
  const collisionWash=mesh(highlights,new THREE.BoxGeometry(.43,.33,.67),new THREE.MeshBasicMaterial({color:colors.collision,transparent:true,opacity:.12,depthWrite:false}),.695,.555,.235);
  const evidenceCanvas=$('evidence-canvas');
  const evidenceContext=evidenceCanvas.getContext('2d',{alpha:false});
  let evidenceWidth=1,evidenceHeight=1;
  const evidenceCamera=new THREE.PerspectiveCamera(37,1.8,.05,60);
  let width=1,height=1,dirty=true,evidenceDirty=true,inView=true,modeMix=0,modeTween=null,cameraTween=null;
  let rigTween=null,tourStart=null,tourStep=-1;
  let rigTarget=new THREE.Vector3(.67,.65,.25);
  const hotspots=Object.fromEntries([...document.querySelectorAll('.hotspot')].map(b=>[b.dataset.finding,b]));
  const targets={collision:new THREE.Vector3(.67,.65,.25),scale:new THREE.Vector3(1.7,1.75,-2.05),access:new THREE.Vector3(2.05,.58,1.57)};
  const focusOffsets={collision:new THREE.Vector3(4.5,4.2,5.2),scale:new THREE.Vector3(5,3.6,5.5),access:new THREE.Vector3(5,5,6)};
  function paintHelpers() {
    highlights.visible=annotations;
    rig.visible=annotations;route.visible=annotations;
    for(const [id,helper] of Object.entries(helpers))helper.visible=id===selected;
    collisionWash.visible=selected==='collision'&&modeMix<.5;
    accessWash.visible=selected==='access';
    helpers.collision.visible=selected==='collision'&&modeMix<.5;
    helpers.scale.update();
  }
  function paintMode(t) {
    modeMix=t;table.position.x=THREE.MathUtils.lerp(.15,-.35,t);
    lamp.scale.setScalar(THREE.MathUtils.lerp(2.65,1,t));
    ottoman.position.set(THREE.MathUtils.lerp(2,-1.3,t),0,THREE.MathUtils.lerp(1.57,1.85,t));
    targets.scale.y=THREE.MathUtils.lerp(1.78,1.26,t);
    if(selected==='scale'){rigTarget.copy(targets.scale);if(rigTween)rigTween.toTarget.copy(targets.scale);}
    paintHelpers();dirty=true;evidenceDirty=true;
  }
  const observerPositions={collision:new THREE.Vector3(-.8,1.3,2.0),scale:new THREE.Vector3(.35,1.8,.4),access:new THREE.Vector3(3.45,1.25,1.0)};
  function evidenceView() {rig.lookAt(rigTarget);evidenceCamera.position.copy(rig.position);evidenceCamera.lookAt(rigTarget);}
  function moveObserver(position,target,duration=1100) {
    rigTween={start:performance.now(),duration:reduceMotion?0:duration,from:rig.position.clone(),to:position.clone(),fromTarget:rigTarget.clone(),toTarget:target.clone()};dirty=true;evidenceDirty=true;
    $('camera-label').classList.add('moving');$('camera-label').querySelector('span').textContent='Acquiring view';
  }
  function explain(message,step=selected) {
    $('inspection-message').textContent=message;
    for(const element of document.querySelectorAll('[data-tour-stage]'))element.classList.toggle('active',element.dataset.tourStage===step);
  }
  function stopTour(message=null) {
    tourStart=null;tourStep=-1;$('tour-btn').setAttribute('aria-pressed','false');$('tour-icon').textContent='▶';$('tour-label').textContent='Replay inspection';
    if(message)explain(message,'report');
  }
  function acquireView(fromTour=false) {
    if(!fromTour)stopTour();
    if(selected==='access'){
      moveObserver(new THREE.Vector3(-.1,1.15,2.65),targets.access,1900);
      explain('Change viewpoint: the ottoman blocks the cabinet’s approach.','access');
      $('detail-title').textContent=adjusted?'The approach area is clear.':'The useful view is on the other side.';
      $('detail-description').textContent=adjusted?'The revised viewpoint shows the clear approach area after the ottoman is moved.':'From the cabinet’s rear, the approach area is hidden. Moving around reveals the ottoman directly in front of it.';
      $('detail-status').textContent=adjusted?'Adjusted example':'Evidence acquired';
    }else{
      const alternative=selected==='collision'?new THREE.Vector3(1.9,.95,1.9):new THREE.Vector3(2.9,1.6,-.3);
      moveObserver(alternative,targets[selected],1400);
      explain(selected==='collision'?'Inspect the contact from another angle.':'Use the desk and chair as scale references.');
    }
  }
  function toggleTour() {
    if(tourStart!==null){stopTour('Inspection paused. Explore the room freely.');return;}
    adjusted=false;updateCopy();modeTween={from:modeMix,to:0,start:performance.now(),duration:reduceMotion?0:600};
    tourStart=performance.now();tourStep=-1;$('tour-btn').setAttribute('aria-pressed','true');$('tour-icon').textContent='Ⅱ';$('tour-label').textContent='Pause inspection';setView('perspective');
  }
  function runTour(now) {
    if(tourStart===null)return;
    const elapsed=now-tourStart;
    const step=elapsed<2600?0:elapsed<6500?1:elapsed<10400?2:elapsed<14900?3:elapsed<18900?4:elapsed<23800?5:6;
    if(step===tourStep)return;tourStep=step;
    if(step===0){explain('A room may look convincing. What should we inspect?','overview');moveObserver(new THREE.Vector3(-2.8,1.4,2.45),new THREE.Vector3(0,.7,0));}
    if(step===1){selectFinding('collision',false,true);explain('Geometry flags a contact. Is it a real defect?','collision');}
    if(step===2){acquireView(true);explain('A closer view exposes the table–chair intersection.','collision');}
    if(step===3){selectFinding('scale',false,true);explain('Geometry alone is not enough: does the lamp fit the scene?','scale');}
    if(step===4){selectFinding('access',false,true);explain('Can the cabinet be used? This view does not show its approach.','access');$('detail-status').textContent='Another view needed';}
    if(step===5){acquireView(true);}
    if(step===6){stopTour('Three principles. Evidence behind each finding. Try “Adjusted”.');setView('perspective');}
  }
  function cameraTo(position,target,zoom=1) {
    cameraTween={start:performance.now(),duration:reduceMotion?0:720,fromPosition:camera.position.clone(),toPosition:position.clone(),fromTarget:controls.target.clone(),toTarget:target.clone(),fromZoom:camera.zoom,toZoom:zoom};
    dirty=true;
  }
  function setView(name) {
    for(const id of ['overview','top']){const on=(id==='top')===(name==='top');$(`${id}-btn`).classList.toggle('active',on);$(`${id}-btn`).setAttribute('aria-pressed',String(on));}
    cameraTo(name==='top'?new THREE.Vector3(0,13,.01):new THREE.Vector3(8.5,9.5,10),new THREE.Vector3(0,.45,0),1.06);
  }
  function select(id,focus,fromTour=false) {
    if(!fromTour)stopTour();
    moveObserver(observerPositions[id],targets[id]);
    $('focus-btn').innerHTML='Acquire another view <span aria-hidden="true">↗</span>';
    if(id==='access'&&!adjusted){$('detail-title').textContent='Can the cabinet be used?';$('detail-description').textContent='The starting camera is behind the cabinet. Acquire another view to inspect its approach area.';$('detail-status').textContent='Another view needed';}
    if(!fromTour)explain(id==='access'?'This view is ambiguous. Move around to gather more evidence.':id==='scale'?'Compare proportions with familiar nearby objects.':'Geometry identifies a contact. Inspect the visual evidence.',id);
    paintHelpers();evidenceView();dirty=true;evidenceDirty=true;
    if(focus){cameraTo(targets[id].clone().add(focusOffsets[id]),targets[id],1.9);for(const button of [$('overview-btn'),$('top-btn')]){button.classList.remove('active');button.setAttribute('aria-pressed','false');}}
  }
  function resize() {
    width=stage.clientWidth;height=stage.clientHeight;
    if(!width||!height)return;
    const aspect=width/height,viewHeight=Math.max(7.5,10.2/aspect);
    camera.left=-viewHeight*aspect/2;camera.right=viewHeight*aspect/2;camera.top=viewHeight/2;camera.bottom=-viewHeight/2;camera.updateProjectionMatrix();renderer.setSize(width,height,false);
    const frame=$('evidence-canvas').parentElement;const ew=frame.clientWidth,eh=frame.clientHeight;
    if(ew&&eh){evidenceWidth=ew;evidenceHeight=eh;evidenceCanvas.width=Math.round(ew*renderer.getPixelRatio());evidenceCanvas.height=Math.round(eh*renderer.getPixelRatio());evidenceCamera.aspect=ew/eh;evidenceCamera.updateProjectionMatrix();}
    dirty=true;evidenceDirty=true;
  }
  new ResizeObserver(resize).observe(stage);
  new ResizeObserver(resize).observe($('evidence-canvas').parentElement);
  new IntersectionObserver(entries=>{inView=entries[0].isIntersecting;dirty=true;evidenceDirty=true;},{rootMargin:'100px'}).observe(stage);
  controls.addEventListener('change',()=>{dirty=true;});
  controls.addEventListener('start',()=>{cameraTween=null;});
  let pointerDown=null;
  canvas.addEventListener('pointerdown',e=>{pointerDown=[e.clientX,e.clientY];});
  canvas.addEventListener('pointerup',e=>{
    if(!pointerDown||Math.hypot(e.clientX-pointerDown[0],e.clientY-pointerDown[1])>5)return;
    const bounds=canvas.getBoundingClientRect();const mouse=new THREE.Vector2((e.clientX-bounds.left)/bounds.width*2-1,-(e.clientY-bounds.top)/bounds.height*2+1);
    const ray=new THREE.Raycaster();ray.setFromCamera(mouse,camera);
    for(const hit of ray.intersectObjects([chair,table,lamp,cabinet,ottoman],true)){
      let object=hit.object;while(object&&!object.userData.finding)object=object.parent;
      if(object){selectFinding(object.userData.finding);break;}
    }
  });
  stage.addEventListener('keydown',e=>{
    if(!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','+','=','-','Home','Escape'].includes(e.key))return;
    e.preventDefault();cameraTween=null;
    if(['Home','Escape'].includes(e.key)){setView('perspective');return;}
    if(['+','=','-'].includes(e.key)){camera.zoom=THREE.MathUtils.clamp(camera.zoom*(e.key==='-'?.9:1.1),.65,2.7);camera.updateProjectionMatrix();}
    else {const offset=camera.position.clone().sub(controls.target);const spherical=new THREE.Spherical().setFromVector3(offset);spherical.theta+=e.key==='ArrowLeft'?.12:e.key==='ArrowRight'?-.12:0;spherical.phi=THREE.MathUtils.clamp(spherical.phi+(e.key==='ArrowUp'?-.1:e.key==='ArrowDown'?.1:0),.025,Math.PI/2-.13);camera.position.copy(controls.target).add(new THREE.Vector3().setFromSpherical(spherical));}
    controls.update();dirty=true;
  });
  canvas.addEventListener('webglcontextlost',e=>{e.preventDefault();failGracefully(new Error('WebGL context lost'));});
  document.addEventListener('visibilitychange',()=>{dirty=true;evidenceDirty=true;});
  function render(now) {
    requestAnimationFrame(render);
    if(document.hidden||!inView)return;
    runTour(now);
    if(rigTween){const a=rigTween;const t=a.duration?Math.min(1,(now-a.start)/a.duration):1;const eased=t*t*(3-2*t);rig.position.lerpVectors(a.from,a.to,eased);rigTarget.lerpVectors(a.fromTarget,a.toTarget,eased);dirty=true;evidenceDirty=true;if(t===1){rigTween=null;$('camera-label').classList.remove('moving');$('camera-label').querySelector('span').textContent='GREP camera';}}
    if(modeTween){const t=modeTween.duration?Math.min(1,(now-modeTween.start)/modeTween.duration):1;const eased=t*t*(3-2*t);paintMode(THREE.MathUtils.lerp(modeTween.from,modeTween.to,eased));if(t===1)modeTween=null;}
    if(cameraTween){const a=cameraTween;const t=a.duration?Math.min(1,(now-a.start)/a.duration):1;const eased=1-Math.pow(1-t,3);camera.position.lerpVectors(a.fromPosition,a.toPosition,eased);controls.target.lerpVectors(a.fromTarget,a.toTarget,eased);camera.zoom=THREE.MathUtils.lerp(a.fromZoom,a.toZoom,eased);camera.updateProjectionMatrix();dirty=true;if(t===1)cameraTween=null;}
    controls.update();
    if(evidenceDirty){
      evidenceView();rig.visible=false;route.visible=false;
      renderer.setViewport(0,0,evidenceWidth,evidenceHeight);
      renderer.render(scene,evidenceCamera);
      const ratio=renderer.getPixelRatio(),ew=evidenceWidth*ratio,eh=evidenceHeight*ratio;
      evidenceContext.drawImage(canvas,0,canvas.height-eh,ew,eh,0,0,evidenceCanvas.width,evidenceCanvas.height);
      rig.visible=annotations;route.visible=annotations;
      renderer.setViewport(0,0,width,height);evidenceDirty=false;dirty=true;
    }
    if(dirty){renderer.render(scene,camera);for(const [id,button] of Object.entries(hotspots)){const pos=targets[id].clone().add(new THREE.Vector3(0,id==='access'?.22:.15,0)).project(camera);button.style.left=`${(pos.x*.5+.5)*width}px`;button.style.top=`${(-pos.y*.5+.5)*height}px`;button.style.visibility=(pos.z>-1&&pos.z<1&&Math.abs(pos.x)<.98&&Math.abs(pos.y)<.95)?'visible':'hidden';}const cp=rig.position.clone().add(new THREE.Vector3(0,.28,0)).project(camera);$('camera-label').style.left=`${(cp.x*.5+.5)*width}px`;$('camera-label').style.top=`${(-cp.y*.5+.5)*height}px`;$('camera-label').style.visibility=Math.abs(cp.x)<.93&&Math.abs(cp.y)<.92?'visible':'hidden';dirty=false;}
  }
  paintHelpers();resize();evidenceView();requestAnimationFrame(render);
  $('loading-state').hidden=true;stage.dataset.ready='true';stage.dataset.scene='synthetic';$('tour-btn').disabled=false;
  if(matchMedia('(pointer:coarse)').matches)$('scene-hint').textContent='Drag to orbit · Pinch to zoom';
  return {select,acquireView,toggleTour,view:setView,setAnnotations(){paintHelpers();dirty=true;evidenceDirty=true;},setAdjusted(value){stopTour();modeTween={from:modeMix,to:value?1:0,start:performance.now(),duration:reduceMotion?0:800};explain(value?'A targeted revision addresses each illustrated issue.':'Return to the original arrangement. Inspect a finding.',value?'report':selected);dirty=true;}};
}
