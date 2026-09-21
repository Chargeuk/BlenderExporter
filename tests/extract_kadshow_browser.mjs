// Extract current application code, without maintaining a rewritten parser/plugin.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {pathToFileURL} from 'node:url';
const [repo,out]=process.argv.slice(2);
const ts=(await import(pathToFileURL(path.join(repo,'fe/client/node_modules/typescript/lib/typescript.js')))).default;
const specs=[['Utils/babylonjs/kadShowSceneLoader.ts','KadShowSceneLoader',
 ['tempIndexContainer','tempMaterialIndexContainer','isDescendantOf','parseMaterialByPredicate','findMaterial','findParent','logOperation','loadDetailLevels','ImportBabylonMeshesAndLights']],
 ['materials/pbrLightmapMaterialPlugin.ts','PbrLightmapMaterialPlugin',null]];
const hashes={};let combined='';
for(const [relative,name,names] of specs){
 const file=path.join(repo,'fe/client/src',relative),text=fs.readFileSync(file,'utf8');
 const source=ts.createSourceFile(file,text,ts.ScriptTarget.Latest,true);
 const declaration=source.statements.find(n=>ts.isClassDeclaration(n)&&n.name.text===name);
 if(!declaration)throw Error('Missing '+name);
 const members=names?declaration.members.filter(n=>names.includes(n.name?.getText(source))):declaration.members;
 if(names&&members.length!==names.length)throw Error('Parser changed');
 const code=`class ${name} ${name==='PbrLightmapMaterialPlugin'?'extends MaterialPluginBase':''} {\n${members.map(n=>n.getText(source)).join('\n')}\n}\nwindow.${name}=${name};`;
 const js=ts.transpileModule(code,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.None}}).outputText;
 combined+='with(BABYLON){\n'+js+'\n}\n';hashes[relative]=crypto.createHash('sha256').update(text).digest('hex');
}
fs.writeFileSync(out,combined);fs.writeFileSync(out+'.sources.json',JSON.stringify(hashes,null,2));
