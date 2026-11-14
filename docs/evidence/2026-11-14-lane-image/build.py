from pathlib import Path
import subprocess,json,time
root=Path('/tmp/vuoro-lane-image-qualified-source-20261114');meta=json.loads((root/'preparation.json').read_text())
argv=['docker','build','--progress=plain','--build-arg','PYTHON_BASE_IMAGE='+meta['base_image_pin'],'--build-arg','VUORO_COMPOSITION_FILE=packages/vuoro-service/composition/lane-adapter-pins.json','--label','io.lanectl.qualification=vuoro-2903-20261114','--label','org.opencontainers.image.revision='+meta['source_commit'],'--tag',meta['target_image'],str(root/'context')]
started=time.monotonic()
with (root/'build.log').open('w') as log: result=subprocess.run(argv,stdout=log,stderr=subprocess.STDOUT)
record={'command':argv,'returncode':result.returncode,'elapsed_s':round(time.monotonic()-started,3),'source_commit':meta['source_commit'],'lane_manifest_sha256':meta['lane_manifest_sha256'],'complete':False}
(root/'build-result.json').write_text(json.dumps(record,indent=2)+'\n')
if result.returncode:raise SystemExit(result.returncode)
image=json.loads(subprocess.check_output(['docker','image','inspect',meta['target_image']],text=True))[0]
assert image['Config']['Labels']['io.lanectl.qualification']=='vuoro-2903-20261114'
record.update({'image_id':image['Id'],'complete':True,'image_labels':image['Config']['Labels']});(root/'build-result.json').write_text(json.dumps(record,indent=2)+'\n');print(json.dumps(record),flush=True)
