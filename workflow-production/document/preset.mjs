import registry from './vendor/preset-v3-registry.json' with {type:'json'};
import {canonical,sha} from './vendor/planner-89c7568/envelope.mjs';

// Pure preset construction, no model/old NPC reads, no assets, no generated content.
export function presetArtifact(payload,taskId){
 const {registration,labels,template}=registry;
 const profile=Object.fromEntries(labels.filter(k=>k!=='traits'&&k!=='numericProfile').map(k=>[k,'']));
 profile.role=payload.character.role;
 profile.traits=structuredClone(template.traits);
 profile.numericProfile={version:1,context:{branchId:'',eventRef:'',sceneId:''},groups:structuredClone(template.numericProfile.groups),
  relationships:[],reviewStatus:'proposal',rules:[]};
 profile.planningCategories=registration.unique.filter(c=>!c.optional).map(c=>({
  id:'category.preset.'+sha(canonical([registration.id,registration.version,taskId,c.id])).slice(0,32),
  name:c.title,content:'',...(c.planningRole?{planningRole:c.planningRole}:{})
 }));
 profile.planningPreset={id:registration.id,version:registration.version,visibleCategoryIds:profile.planningCategories.map(c=>c.id)};
 return {schemaVersion:'workflow.npc-preset-artifact.v1',taskId,projectId:payload.projectId,
  character:{...payload.character},profile,presetRegistrySha256:sha(canonical(registration)),
  contentGenerated:false,oldDataReused:false};
}
