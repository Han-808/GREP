import * as THREE from 'three';
import { OrbitControls } from './vendor/OrbitControls.js';
import { buildRealisticRoom } from './realistic-room.js?v=21d1f30cdf22';

const $ = (id) => document.getElementById(id);
const stage = $('scene-stage');
const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;
const colors = { collision: '#527bb2', scale: '#8d6bb0', access: '#267b72' };
const textColors = { collision: '#355e92', scale: '#71508f', access: '#226d64' };
const findings = {
  collision: { objects: 'Coffee table · Armchair', diagnosis: 'Move the table away from the armchair.', adjustedDiagnosis: 'The report’s fix separates the two objects.', number: '01', metric: 'Collision', question: 'Do the table and armchair intersect?', evidence: 'GREP inspects the contact between their surfaces.', category: 'Physical Validity', title: 'The table intersects the armchair.',
    description: '',
    adjustedTitle: 'The surfaces no longer intersect.', adjustedDescription: '',
    adjustedLabel: 'Objects separated', originalLabel: 'Object collision' },
  scale: { objects: 'Desk lamp · Desk · Chair', diagnosis: 'Reduce the lamp’s size.', adjustedDiagnosis: 'The report’s fix reduces the lamp’s size.', number: '02', metric: 'Scale', question: 'Is the lamp reasonably sized for this desk?', evidence: 'GREP compares the lamp with the desk and chair.', category: 'Visual Coherence', title: 'The lamp is oversized.',
    description: '',
    adjustedTitle: 'The lamp is in proportion.', adjustedDescription: '',
    adjustedLabel: 'Lamp resized', originalLabel: 'Scale mismatch' },
  access: { number: '03', metric: 'Function', category: 'Human-Use', objects: 'Cabinet door · Ottoman',
    question: 'Can the cabinet door open fully?',
    evidence: 'Evidence is incomplete. GREP requests the cabinet’s front view.',
    title: 'The hinge is hidden from this view.', description: '',
    adjustedTitle: 'Check the revised arrangement.', adjustedDescription: '',
    adjustedLabel: 'Door clearance', originalLabel: 'Cabinet door' },
};
let selected = 'access';
let adjusted = false;
let accessPhase = 'unseen';
let doorResult = null;
let arrangementMoving = false;
let viewMoving = false;
let pendingRevisionFocus = false;
let annotations = true;
let sceneAPI = null;
let evidenceOperations = { measure: false, existing: false, render: false };
let visualEvidencePhase = 'unseen';
let visualEvidenceRevision = 0;

function updateCopy() {
  const finding = findings[selected];
  const hasDiagnosis = selected === 'access' ? ['blocked', 'clear'].includes(accessPhase) : visualEvidencePhase === 'ready';
  const interactionBusy = !sceneAPI || arrangementMoving || viewMoving || (selected === 'access' && ['moving', 'opening'].includes(accessPhase));
  for (const button of document.querySelectorAll('[data-finding]')) {
    const active = button.dataset.finding === selected;
    button.classList.toggle('selected', active);
    if (button.tagName === 'BUTTON') button.setAttribute('aria-pressed', String(active));
    if (button.classList.contains('finding-row')) button.querySelector('strong').textContent = findings[button.dataset.finding].question;
  }
  $('check-question').textContent = finding.question;
  $('check-evidence').textContent = finding.evidence;
  $('detail-category').textContent = finding.category;
  $('detail-category').style.color = textColors[selected];
  $('detail-title').textContent = adjusted ? finding.adjustedTitle : finding.title;
  $('detail-description').textContent = adjusted ? finding.adjustedDescription : finding.description;
  $('detail-status').textContent = adjusted ? 'Adjusted' : 'Finding';
  $('detail-status').classList.toggle('resolved', adjusted);
  $('diagnosis-objects').textContent = finding.objects;
  $('diagnosis-result').textContent = adjusted ? finding.adjustedDiagnosis || '' : finding.diagnosis || '';
  $('door-test-btn').hidden = selected !== 'access';
  $('door-result').hidden = selected !== 'access' || !['blocked', 'clear'].includes(accessPhase);
  $('door-test-btn').disabled = !sceneAPI || arrangementMoving || ['unseen', 'moving', 'opening'].includes(accessPhase);
  $('door-test-label').textContent = ['blocked', 'clear'].includes(accessPhase) ? 'Measure swing clearance again' : 'Measure swing clearance';
  $('focus-label').textContent = selected === 'access' ? 'Render cabinet front' : 'Render another view';
  $('focus-btn').disabled = interactionBusy;
  $('focus-btn').classList.toggle('is-next', selected === 'access' ? accessPhase === 'unseen' : visualEvidencePhase === 'unseen');
  $('door-test-btn').classList.toggle('is-next', selected === 'access' && accessPhase === 'ready');
  $('adjust-finding-label').textContent = adjusted ? 'Restore original' : { collision: 'Apply report fix: move the table', scale: 'Apply report fix: resize the lamp', access: 'Apply report fix: move the ottoman' }[selected];
  $('adjust-finding-btn').disabled = interactionBusy || !hasDiagnosis;
  $('adjust-finding-btn').classList.toggle('is-next', !adjusted && hasDiagnosis);
  if (selected === 'access') {
    const angle = Math.round(doorResult?.angleDeg || 0);
    const copy = {
      unseen: ['View needed', 'The hinge is hidden from this view.', '', '', ''],
      moving: ['Rendering', 'GREP is rendering the cabinet’s front view…', '', '', ''],
      ready: ['Ready', 'The front view reveals the hinge and ottoman.', '', '', ''],
      opening: ['Measuring', 'GREP measures clearance along the door’s swing…', '', '', ''],
      blocked: ['Blocked', 'The ottoman blocks the door.', '', 'Move the ottoman outside the door’s swing.', `Opens ${angle}° of 90°`],
      clear: ['Clear', 'The door clears the ottoman.', '', 'The report’s fix leaves the door’s swing clear.', 'Opens 90° of 90°'],
    }[accessPhase];
    $('detail-status').textContent = copy[0];
    $('detail-title').textContent = copy[1];
    $('detail-description').textContent = copy[2];
    $('diagnosis-result').textContent = copy[3];
    $('door-result').textContent = copy[4];
    $('detail-status').classList.toggle('resolved', accessPhase === 'clear');
    if (accessPhase === 'ready') $('check-evidence').textContent = 'Front view acquired. GREP can measure the door’s swing clearance.';
    if (['opening', 'blocked', 'clear'].includes(accessPhase)) $('check-evidence').textContent = 'GREP measures the swing clearance from the cabinet and ottoman geometry.';
  } else if (!hasDiagnosis) {
    const rendering = visualEvidencePhase === 'moving';
    $('check-evidence').textContent = selected === 'collision' ? 'GREP requests a closer view of the table and armchair.' : 'GREP requests a closer view of the lamp, desk, and chair.';
    $('detail-status').textContent = rendering ? 'Rendering' : 'View needed';
    $('detail-title').textContent = rendering ? 'GREP is rendering another view…' : selected === 'collision' ? 'The contact needs a closer view.' : 'The lamp needs a closer comparison.';
    $('detail-status').classList.toggle('resolved', false);
  }
  $('door-illustration-note').hidden = selected !== 'access';
  for (const chip of document.querySelectorAll('[data-evidence-operation]')) {
    const active = evidenceOperations[chip.dataset.evidenceOperation];
    chip.classList.toggle('used', active);
    chip.setAttribute('aria-label', `${chip.textContent.trim()}: ${active ? 'used in this inspection' : 'not used in this inspection'}`);
  }
  $('detail-description').hidden = !$('detail-description').textContent;
  $('diagnosis-result').hidden = !hasDiagnosis || !$('diagnosis-result').textContent;
  $('diagnosis-step').hidden = false;
  $('diagnosis-content').hidden = !hasDiagnosis;
  $('diagnosis-pending').hidden = hasDiagnosis;
  $('diagnosis-pending').textContent = 'Pending';
  for (const step of document.querySelectorAll('[data-eval-step]')) {
    step.dataset.state = { check: 'done', evidence: hasDiagnosis ? 'done' : 'current', diagnosis: hasDiagnosis ? 'current' : 'pending' }[step.dataset.evalStep];
  }
  $('evidence-label').textContent = `${finding.metric.toUpperCase()} / EVIDENCE`;
  $('evidence-canvas').setAttribute('aria-label', `Evidence view: ${finding.metric}`);
  $('finding-count').textContent = finding.metric;
  $('scene-version-label').textContent = adjusted ? 'Adjusted arrangement' : 'Original arrangement';
  for (const mode of ['original', 'adjusted']) {
    const active = (mode === 'adjusted') === adjusted;
    $(`${mode}-btn`).classList.toggle('selected', active);
    $(`${mode}-btn`).setAttribute('aria-pressed', String(active));
  }
}

function selectFinding(id, focus = false, fromTour = false) {
  pendingRevisionFocus = false;
  evidenceOperations = { measure: false, existing: false, render: false };
  visualEvidencePhase = 'unseen'; visualEvidenceRevision++;
  if (id === 'access') { accessPhase = 'unseen'; doorResult = null; }
  selected = id; updateCopy(); sceneAPI?.select(id, focus, fromTour);
}
function setArrangement(value, returnFocus = false) {
  if (!sceneAPI || value === adjusted) return;
  pendingRevisionFocus = returnFocus;
  adjusted = value;
  evidenceOperations.measure = false;
  visualEvidencePhase = 'unseen'; visualEvidenceRevision++;
  if (selected !== 'access') evidenceOperations.render = false;
  if (!['unseen','moving'].includes(accessPhase)) accessPhase = 'ready';
  doorResult = null; updateCopy(); sceneAPI.setAdjusted(adjusted);
}

function restoreRevisionFocus() {
  if (!pendingRevisionFocus || arrangementMoving || viewMoving) return;
  const target = selected === 'access' && accessPhase === 'ready' ? $('door-test-btn') : $('focus-btn');
  if (target.disabled || target.hidden) return;
  pendingRevisionFocus = false;
  target.focus({ preventScroll: true });
}

document.querySelectorAll('[data-finding]').forEach(button => button.addEventListener('click', () => selectFinding(button.dataset.finding)));
for (const mode of ['original', 'adjusted']) $(`${mode}-btn`).addEventListener('click', () => setArrangement(mode === 'adjusted'));
$('adjust-finding-btn').addEventListener('click', () => setArrangement(!adjusted, true));
$('focus-btn').addEventListener('click', () => sceneAPI?.acquireView());
$('tour-btn').addEventListener('click', () => sceneAPI?.toggleTour());
$('door-test-btn').addEventListener('click', () => sceneAPI?.testDoor());
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
  for (const id of ['focus-btn','door-test-btn','adjust-finding-btn','overview-btn','top-btn','reset-btn','annotations-btn']) $(id).disabled = true;
}

updateCopy();
createRoom().then(api => {
  sceneAPI = api; updateCopy();
  if (adjusted) sceneAPI.setAdjusted(true);
  if (selected !== 'collision') sceneAPI.select(selected, false);
}).catch(failGracefully);

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
  const {chair,table,lamp,cabinet,ottoman,rig,route,cabinetMechanism}=await buildRealisticRoom(scene,renderer,(count,total)=>{
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
  const collisionWash=mesh(highlights,new THREE.BoxGeometry(.43,.33,.67),new THREE.MeshBasicMaterial({color:colors.collision,transparent:true,opacity:.12,depthWrite:false}),.695,.555,.235);
  const evidenceCanvas=$('evidence-canvas');
  const evidenceContext=evidenceCanvas.getContext('2d',{alpha:false});
  let evidenceWidth=1,evidenceHeight=1;
  const evidenceCamera=new THREE.PerspectiveCamera(37,1.8,.05,60);
  let width=1,height=1,dirty=true,evidenceDirty=true,inView=true,evidenceInView=true,modeMix=0,modeTween=null,cameraTween=null;
  let rigTween=null,tourStart=null,tourStep=-1,doorTween=null,doorProgress=0;
  let rigTarget=new THREE.Vector3(.67,.65,.25);
  const hotspots=Object.fromEntries([...document.querySelectorAll('.hotspot')].map(b=>[b.dataset.finding,b]));
  const targets={collision:new THREE.Vector3(.67,.65,.25),scale:new THREE.Vector3(1.7,1.75,-2.05),access:new THREE.Vector3(2.35,.57,1.8)};
  const focusOffsets={collision:new THREE.Vector3(4.5,4.2,5.2),scale:new THREE.Vector3(5,3.6,5.5),access:new THREE.Vector3(-3.4,2.5,3.3)};
  function paintHelpers() {
    highlights.visible=annotations;
    rig.visible=annotations;route.visible=annotations;
    for(const [id,helper] of Object.entries(helpers))helper.visible=id===selected;
    collisionWash.visible=selected==='collision'&&modeMix<.5;
    cabinetMechanism.setGuideVisible(annotations && selected === 'access');
    helpers.collision.visible=selected==='collision'&&modeMix<.5;
    helpers.scale.update();
  }
  function paintMode(t) {
    modeMix=t;table.position.x=THREE.MathUtils.lerp(.15,-.35,t);
    lamp.scale.setScalar(THREE.MathUtils.lerp(2.65,1,t));
    ottoman.position.set(THREE.MathUtils.lerp(2.10,1.57,t),0,THREE.MathUtils.lerp(1.64,2.27,t));
    cabinetMechanism.setOpenProgress(doorProgress);
    targets.scale.y=THREE.MathUtils.lerp(1.78,1.26,t);
    if(selected==='scale'){rigTarget.copy(targets.scale);if(rigTween)rigTween.toTarget.copy(targets.scale);}
    paintHelpers();dirty=true;evidenceDirty=true;
  }
  const observerPositions={collision:new THREE.Vector3(-.8,1.3,2.0),scale:new THREE.Vector3(.35,1.8,.4),access:new THREE.Vector3(4.65,2.0,2.55)};
  function evidenceView() {rig.lookAt(rigTarget);evidenceCamera.position.copy(rig.position);evidenceCamera.lookAt(rigTarget);}
  function moveObserver(position,target,duration=1100,onComplete=null) {
    rigTween={onComplete,start:performance.now(),duration:reduceMotion?0:duration,from:rig.position.clone(),to:position.clone(),fromTarget:rigTarget.clone(),toTarget:target.clone()};dirty=true;evidenceDirty=true;
    viewMoving=true;updateCopy();
    $('camera-label').classList.add('moving');$('camera-label').querySelector('span').textContent='Acquiring view';
  }
  function explain(message,step=selected) {
    $('inspection-message').textContent=message;
    for(const element of document.querySelectorAll('[data-tour-stage]'))element.classList.toggle('active',element.dataset.tourStage===step);
  }
  function stopTour(message=null) {
    tourStart=null;tourStep=-1;$('tour-btn').setAttribute('aria-pressed','false');$('tour-icon').textContent='▶';$('tour-label').textContent='Walkthrough';
    if(message)explain(message,'report');
  }
  function acquireView(fromTour=false) {
    if (arrangementMoving || viewMoving || (selected === 'access' && accessPhase === 'opening')) return;
    if(!fromTour)stopTour();
    evidenceOperations.render = true;
    if(selected==='access'){
      accessPhase='moving';doorResult=null;doorTween=null;doorProgress=0;cabinetMechanism.setOpenProgress(0);updateCopy();
      moveObserver(new THREE.Vector3(.6,1.8,3.25),targets.access,1600,()=>{
        if(selected==='access'){accessPhase='ready';updateCopy();explain('GREP has a front view of the hinged door and ottoman. Measure the swing clearance next.','access');}
      });
      cameraTo(targets.access.clone().add(focusOffsets.access),targets.access,1.65);
      explain('Evidence is incomplete. GREP requests a new view of the cabinet front.','access');
    }else{
      const findingId = selected, revision = visualEvidenceRevision;
      visualEvidencePhase = 'moving';
      const alternative=selected==='collision'?new THREE.Vector3(1.9,.95,1.9):new THREE.Vector3(2.9,1.6,-.3);
      moveObserver(alternative,targets[selected],1400,()=>{
        if (selected !== findingId || visualEvidenceRevision !== revision) return;
        visualEvidencePhase = 'ready'; updateCopy();
        explain(adjusted ? findings[findingId].adjustedTitle : findings[findingId].title, 'report');
      });
      explain(selected==='collision'?'GREP renders another view across the contact area.':'GREP renders another view of the lamp, desk, and chair.');
    }
  }
  function testDoor(fromTour=false) {
    if(selected!=='access'||arrangementMoving||['unseen','moving','opening'].includes(accessPhase))return;
    if(!fromTour)stopTour();
    evidenceOperations.measure = true;
    doorProgress=0;cabinetMechanism.setOpenProgress(0);doorResult=null;accessPhase='opening';updateCopy();
    doorTween={start:performance.now(),duration:reduceMotion?0:1500,target:Math.min(1,cabinetMechanism.getState().maxAngleDeg/90)};
    explain('GREP measures first contact across the door’s swing. The animation illustrates that measurement.','access');dirty=true;evidenceDirty=true;
  }
  function resetDoor() {
    doorTween=null;doorProgress=0;doorResult=null;cabinetMechanism.setOpenProgress(0);
  }
  function toggleTour() {
    if(tourStart!==null){stopTour('Inspection paused. Explore the room freely.');return;}
    pendingRevisionFocus = false;
    evidenceOperations = { measure: false, existing: false, render: false };
    visualEvidencePhase = 'unseen'; visualEvidenceRevision++;
    accessPhase = 'unseen';resetDoor();
    adjusted=false;arrangementMoving=true;updateCopy();modeTween={from:modeMix,to:0,start:performance.now(),duration:reduceMotion?0:600};
    tourStart=performance.now();tourStep=-1;$('tour-btn').setAttribute('aria-pressed','true');$('tour-icon').textContent='Ⅱ';$('tour-label').textContent='Pause';setView('perspective');
  }
  function runTour(now) {
    if(tourStart===null)return;
    const elapsed=now-tourStart;
    const step=elapsed<2600?0:elapsed<6100?1:elapsed<10000?2:elapsed<13900?3:elapsed<17400?4:elapsed<20700?5:elapsed<24500?6:7;
    if(step===tourStep)return;tourStep=step;
    if(step===0){explain('A furnished room creates relationships worth checking. Start with its objects and their uses.','overview');moveObserver(new THREE.Vector3(-2.8,1.4,2.45),new THREE.Vector3(0,.7,0));}
    if(step===1){selectFinding('collision',false,true);explain('Physical Validity: do the table and armchair occupy the same space?','collision');}
    if(step===2){acquireView(true);}
    if(step===3){selectFinding('scale',false,true);explain('Visual Coherence: is the lamp reasonably sized for this desk?','scale');}
    if(step===4){selectFinding('access',false,true);explain('Human-Use: does the cabinet door have enough room to open? Its hinge is hidden from this side.','access');}
    if(step===5){acquireView(true);}
    if(step===6){testDoor(true);}
    if(step===7){stopTour('The report identifies the ottoman. Apply the fix, then measure the swing clearance again.');}
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
    resetDoor();
    moveObserver(observerPositions[id],targets[id],1100,fromTour&&id==='scale'?()=>{
      if(tourStart!==null&&selected==='scale')acquireView(true);
    }:null);
    if(!fromTour)explain(id==='access'?'GREP needs a front view before measuring the door’s swing clearance.':id==='scale'?'GREP compares the lamp with the desk and chair.':'GREP checks whether the table and armchair intersect.',id);
    paintHelpers();evidenceView();dirty=true;evidenceDirty=true;
    if(focus){cameraTo(targets[id].clone().add(focusOffsets[id]),targets[id],1.65);for(const button of [$('overview-btn'),$('top-btn')]){button.classList.remove('active');button.setAttribute('aria-pressed','false');}}
    else if(!fromTour)setView('perspective');
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
  new IntersectionObserver(entries=>{evidenceInView=entries[0].isIntersecting;evidenceDirty=true;},{rootMargin:'100px'}).observe(evidenceCanvas.parentElement);
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
  document.addEventListener('visibilitychange',()=>{if(document.hidden&&tourStart!==null)stopTour('Inspection paused. Replay the inspection when ready.');dirty=true;evidenceDirty=true;});
  function render(now) {
    requestAnimationFrame(render);
    if(document.hidden)return;
    if(!inView&&!evidenceInView&&tourStart!==null)stopTour('Inspection paused. Replay the inspection when the room is visible.');
    if(!inView&&!evidenceInView&&!rigTween&&!doorTween&&!modeTween&&!cameraTween&&tourStart===null)return;
    runTour(now);
    if(rigTween){const a=rigTween;const t=a.duration?Math.min(1,(now-a.start)/a.duration):1;const eased=t*t*(3-2*t);rig.position.lerpVectors(a.from,a.to,eased);rigTarget.lerpVectors(a.fromTarget,a.toTarget,eased);dirty=true;evidenceDirty=true;if(t===1){rigTween=null;viewMoving=false;$('camera-label').classList.remove('moving');$('camera-label').querySelector('span').textContent='GREP camera';a.onComplete?.();updateCopy();restoreRevisionFocus();}}
    if(modeTween){const t=modeTween.duration?Math.min(1,(now-modeTween.start)/modeTween.duration):1;const eased=t*t*(3-2*t);paintMode(THREE.MathUtils.lerp(modeTween.from,modeTween.to,eased));if(t===1){modeTween=null;arrangementMoving=false;updateCopy();restoreRevisionFocus();}}
    if(doorTween){
      const t=doorTween.duration?Math.min(1,(now-doorTween.start)/doorTween.duration):1;
      doorProgress=(1-Math.pow(1-t,3))*doorTween.target;doorResult=cabinetMechanism.setOpenProgress(doorProgress);dirty=true;evidenceDirty=true;
      if(t===1){doorProgress=1;doorResult=cabinetMechanism.setOpenProgress(1);doorTween=null;accessPhase=doorResult.blocked?'blocked':'clear';updateCopy();explain(doorResult.blocked?'The measurement finds the ottoman in the door’s swing. Apply the report’s fix, then measure again.':'The report’s fix leaves the door’s full swing clear.','report');}
    }
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
  return {select,acquireView,testDoor,toggleTour,view:setView,setAnnotations(){paintHelpers();dirty=true;evidenceDirty=true;},setAdjusted(value){stopTour();resetDoor();arrangementMoving=true;updateCopy();modeTween={from:modeMix,to:value?1:0,start:performance.now(),duration:reduceMotion?0:800};explain(selected==='access'?(value?'The report’s fix moves the ottoman. Measure swing clearance again.':'The ottoman is close to the cabinet again. Obtain evidence and measure the swing clearance.'):(value?'The adjusted room applies the report’s fixes.':'Return to the original arrangement.'),value?'report':selected);dirty=true;}};
}
