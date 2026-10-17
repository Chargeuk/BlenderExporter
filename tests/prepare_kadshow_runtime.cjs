// Task-specific harness adapter. Consumer function bodies are extracted unchanged.
const fs=require('node:fs'),path=require('node:path'),cp=require('node:child_process'),crypto=require('node:crypto');
const [repo,app]=process.argv.slice(2); if(!repo||!app)throw Error('Usage: prepare_kadshow_runtime.cjs REPO EXTRACTED_APP');
const ts=require(path.join(repo,'fe/client/node_modules/typescript'));

if(!fs.existsSync(app))throw Error('Run the shared extract_kadshow_browser.mjs helper first');
function read(rel){return fs.readFileSync(path.join(repo,'fe/client/src',rel),'utf8')}
function transpile(code){return ts.transpileModule(code,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.None}}).outputText}
const hashes=JSON.parse(fs.readFileSync(app+'.sources.json','utf8'));
function declarations(rel,predicate){let text=read(rel);hashes[rel]=crypto.createHash('sha256').update(text).digest('hex');let source=ts.createSourceFile(rel,text,ts.ScriptTarget.Latest,true);return source.statements.filter(predicate).map(n=>n.getText(source).replace(/^export\s+/, '')).join('\n')}
let extra='\nconst process={env:{NODE_ENV:"production",REACT_APP_LIGHTING_DEBUG:"false"}};\n';
extra+=transpile(declarations('materials/lightingSettings.ts',n=>!ts.isImportDeclaration(n)&&!ts.isExportDeclaration(n)));
const funcs=declarations('Utils/Utility/mergeMeshes.ts',n=>ts.isFunctionDeclaration(n));
const dispose=declarations('Utils/Utility/safeDispose.ts',n=>ts.isVariableStatement(n));
extra+='\nwith(BABYLON){with(_){'+transpile(dispose+'\n'+funcs)+'\nwindow.mergeOptimisedChunkMeshes=mergeOptimisedChunkMeshes;window.mergeSimilarMeshes=mergeSimilarMeshes;}}\n';
const env=read('Components/interactables/Environment.tsx'),ast=ts.createSourceFile('Environment.tsx',env,ts.ScriptTarget.Latest,true),ifs=[];
function visit(n){if(ts.isIfStatement(n)){const cond=n.expression.getText(ast);if(cond.includes('mesh.name.toLowerCase().includes("optimise_chunk")')||cond==='mesh.name.toLowerCase().includes("alwaysselectasactivemesh")')ifs.push(n.getText(ast));}ts.forEachChild(n,visit)}visit(ast);
if(ifs.length!==2)throw Error('Consumer metadata branches changed');
extra+=transpile('function applyCurrentChunkMetadata(mesh){'+ifs.join('\n')+'}');
const materialFunctions=declarations('Components/interactables/Environment.tsx',n=>ts.isFunctionDeclaration(n)&&['convertToStandardMaterial','assignLightmapToMeshMaterial'].includes(n.name?.text));
const conversionFlag=declarations('Components/interactables/Environment.tsx',n=>ts.isVariableStatement(n)&&n.declarationList.declarations.some(d=>d.name.getText()==='enableOptimisedConversion'));
const lightBranches=[];function visitLights(n){if(ts.isIfStatement(n)&&['!lowerCaseLightName.includes("diffuse")','!lowerCaseLightName.includes("highlights")'].includes(n.expression.getText(ast)))lightBranches.push(n.getText(ast));ts.forEachChild(n,visitLights)}visitLights(ast);
if(lightBranches.length!==2)throw Error('Client light channel branches changed');
extra+='\nwith(BABYLON){'+transpile(conversionFlag+'\n'+materialFunctions+'\nfunction applyCurrentLightChannels(light){const lowerCaseLightName=light.name.toLowerCase();'+lightBranches.join('\n')+'}')+'\nwindow.convertToStandardMaterial=convertToStandardMaterial;window.assignLightmapToMeshMaterial=assignLightmapToMeshMaterial;window.applyCurrentLightChannels=applyCurrentLightChannels;}\n';
extra+='\nwith(BABYLON){'+transpile(declarations('materials/colourAdjustment.ts',n=>!ts.isImportDeclaration(n)&&!ts.isExportDeclaration(n)))+'\nwindow.getColourAdjustment=getColourAdjustment;window.loadColourAdjustment=loadColourAdjustment;}\n';
fs.appendFileSync(app,extra);fs.writeFileSync(app+'.sources.json',JSON.stringify(hashes,null,2));
console.log('Extracted current loader, lighting, metadata and both merge functions; source hashes recorded.');
