import {copyFileSync,mkdirSync} from 'node:fs';
mkdirSync('app/static/vendor',{recursive:true});
for (const [src,dst] of [['dist/wavesurfer.esm.js','wavesurfer.js'],['dist/plugins/regions.esm.js','regions.js']]) copyFileSync('node_modules/wavesurfer.js/'+src,'app/static/vendor/'+dst);

for (const name of ["swagger-ui.css","swagger-ui-bundle.js"]) copyFileSync("node_modules/swagger-ui-dist/"+name,"app/static/vendor/"+name);
