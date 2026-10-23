"""Independent F1b source/SDK regressions, admitted by the root repair decisions.

All producers are actual installed qualified compiler/native SDKs. Mutations and
sentinels live in disposable copies; original product/tool inputs are read-only.
"""
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest
from vuoro_contracts import ContractBundle, ContractError

ROOT = Path(__file__).resolve().parents[3]
TOOLS = ROOT / "tools/contracts"
SOURCE = TOOLS / "fixtures/probe"
HELPERS = TOOLS / "templates/tests"


def load_generator(root=ROOT):
    spec = importlib.util.spec_from_file_location("regression_generator", root / "scripts/contracts/generate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def invoke(generator, output, source=SOURCE):
    return generator.main(["--source", str(source), "--output", str(output), "--contract-id", "probe/v1", "--write"])


def executable(name):
    path = Path(os.environ[name])
    assert path.is_absolute() and path.is_file(), "qualified_executable_required"
    return str(path)


def inventory(root):
    return {p.relative_to(root).as_posix(): sha256(p.read_bytes()).hexdigest() for p in root.rglob("*") if p.is_file()}


def run(command, env=None):
    result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def bundle(root):
    return ContractBundle.load(root, integrity_sha256=sha256((root / "integrity.json").read_bytes()).hexdigest())


@pytest.fixture(scope="module")
def real_projects(tmp_path_factory):
    work = tmp_path_factory.mktemp("f1b-independent-native-regressions")
    generator = load_generator()
    output = work / "baseline"
    assert invoke(generator, output) == 0
    contract = bundle(output)
    assert contract.validate_request("submit", {"revision":"12", "enabled":True, "nested":{"label":"x"}})
    variants = {"base": output}
    def variant(kind):
        if kind not in variants:
            source = work / (kind + "-source")
            shutil.copytree(SOURCE, source)
            p = source / "main.tsp"
            text = p.read_text()
            if kind == "tagged": text = text.replace('@route("/probe/{key}") @post', '@tag("Owner") @route("/probe/{key}") @post')
            elif kind == "header": text = text.replace("op submit(@path key: Key, @body input: Input)", 'op submit(@path key: Key, @header("x-token") token: Key, @body input: Input)')
            elif kind == "query": text = text.replace("op submit(@path key: Key, @body input: Input)", "op submit(@path key: Key, @body input: Input, @query selector?: Key)")
            elif kind == "underscore":
                text = text.replace("op submit(", "op submit_item(")
                metadata_path = source / "contract.json"
                metadata = json.loads(metadata_path.read_bytes())
                for key in ("exposures", "byteBounds"): metadata[key]["submit_item"] = metadata[key].pop("submit")
                metadata_path.write_text(json.dumps(metadata))
            else: raise AssertionError("unknown_variant")
            assert text != p.read_text()
            p.write_text(text)
            out = work / kind
            assert invoke(generator, out, source) == 0
            bundle(out)
            variants[kind] = out
        return variants[kind]
    return variant


@pytest.mark.parametrize("scenario,kind", [
    ("routing_override","base"), ("unknown_kwargs","base"),
    ("serialized_path","base"), ("serialized_query","query"),
    ("tagged","tagged"), ("header","header"), ("serialized_header","header"),
])
def test_real_python_native_regression(real_projects, scenario, kind):
    output = real_projects(kind)  # variant production failure is a test failure, not fixture setup error
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(ROOT / "packages/vuoro-contracts/src"))
    observed = run([executable("VUORO_CONTRACT_PYTHON"), str(HELPERS / "python_sdk_regressions.py"), str(output), scenario], env)
    assert json.loads(observed) == {"scenario": scenario, "result": "pass"}


def build_ts_regression(output, work):
    shutil.copytree(output / "clients/typescript", work / "client")
    path = work / "client/client/client.gen.ts"
    text = path.read_text()
    anchor = "request = new Request(url, requestInit);"
    assert text.count(anchor) == 1, "qualified_native_request_anchor_changed"
    replacement = """const regressionUrl = new URL(url);
if ((globalThis as any).__regressionMutation === 'serialized_path') regressionUrl.pathname = '/probe/' + 'x'.repeat(65);
if ((globalThis as any).__regressionMutation === 'serialized_query') regressionUrl.searchParams.set('selector','x'.repeat(65));
request = new Request(regressionUrl, requestInit);
if ((globalThis as any).__regressionMutation === 'serialized_header') request.headers.set('x-token','x'.repeat(65));"""
    text = text.replace(anchor, replacement)
    serializer = "opts.serializedBody = opts.bodySerializer(opts.body) as string | undefined;"
    assert text.count(serializer) == 1, "qualified_native_serializer_anchor_changed"
    text = text.replace(serializer, "(globalThis as any).__regressionSerializers = ((globalThis as any).__regressionSerializers ?? 0) + 1;\n" + serializer)
    native_anchor = "const request: Client['request'] = async (options) => {"
    assert text.count(native_anchor) == 1, "qualified_native_entry_anchor_changed"
    text = text.replace(native_anchor, native_anchor + "\n(globalThis as any).__regressionNative = ((globalThis as any).__regressionNative ?? 0) + 1;")
    path.write_text(text)
    replacements = {"__ATTACHMENT__":str(work / "client/validated-sdk.ts"), "__SDK__":str(work / "client/sdk.gen.ts"), "__CLIENT__":str(work / "client/client/index.ts"), "__DECODER__":str(work / "client/strict-json.ts")}
    runner = (HELPERS / "typescript_sdk_regressions.ts").read_text()
    for key, value in replacements.items(): runner = runner.replace(key, value)
    entry = work / "regression.ts"
    entry.write_text(runner)
    program = """import {pathToFileURL} from 'node:url';
const [api,entry,out,mods]=process.argv.slice(1);const {build}=await import(pathToFileURL(api).href);
await build({entryPoints:[entry],outfile:out,bundle:true,platform:'node',format:'esm',target:'node24',nodePaths:[mods],logLevel:'error'});"""
    out = work / "regression.mjs"
    run([executable("VUORO_CONTRACT_NODE"), "--input-type=module", "-e", program, str(TOOLS / "node_modules/esbuild/lib/main.js"), str(entry), str(out), str(TOOLS / "node_modules")])
    return out


@pytest.mark.parametrize("scenario,kind", [("serialized_path","base"), ("serialized_query","query"), ("serialized_header","header"), ("unsupported_groups","base"), ("tagged","tagged"), ("header","header")])
def test_real_typescript_native_regression(real_projects, tmp_path, scenario, kind):
    oracle = build_ts_regression(real_projects(kind), tmp_path)
    observed = run([executable("VUORO_CONTRACT_NODE"), str(oracle), scenario])
    assert json.loads(observed) == {"scenario":scenario, "result":"pass"}


@pytest.fixture(scope="module")
def disposable_project(tmp_path_factory):
    """One immutable project skeleton with authenticated installed-artifact links."""
    root = tmp_path_factory.mktemp("f1b-regression-qualified-project")
    for name in ("scripts/contracts", "contracts", "packages/vuoro-contracts/src", "tools/contracts"):
        shutil.copytree(ROOT / name, root / name, ignore=shutil.ignore_patterns("node_modules", ".venv", "__pycache__", "*.pyc"))
    for name in ("node_modules", ".venv"):
        (root / "tools/contracts" / name).symlink_to(TOOLS / name, target_is_directory=True)
    generator = load_generator(root)
    output = root / "baseline"
    assert invoke(generator, output, root / "tools/contracts/fixtures/probe") == 0
    bundle(output)
    return root


def copy_project(skeleton, tmp_path):
    root = tmp_path / "project"
    shutil.copytree(skeleton, root, symlinks=True, ignore=shutil.ignore_patterns("baseline"))
    return root, load_generator(root), root / "tools/contracts/fixtures/probe"


def test_current_shared_semantics_and_provenance(disposable_project, tmp_path):
    root, generator, source = copy_project(disposable_project, tmp_path)
    out = root / "changed-shared"
    p = root / "contracts/primitives/main.tsp"
    p.write_text(p.read_text().replace("scalar NonnegativeInt64Decimal extends string;", "@maxLength(1)\nscalar NonnegativeInt64Decimal extends string;"))
    installed_before = inventory(TOOLS / "node_modules/@vuoro/contracts")
    assert invoke(generator, out, source) == 0
    assert inventory(TOOLS / "node_modules/@vuoro/contracts") == installed_before
    contract = bundle(out)
    assert contract.validate_request("submit", {"revision":"1","enabled":True,"nested":{"label":"x"}})
    with pytest.raises(ContractError): contract.validate_request("submit", {"revision":"12","enabled":True,"nested":{"label":"x"}})
    assert contract.manifest["provenance"] != bundle(disposable_project / "baseline").manifest["provenance"]


@pytest.mark.parametrize("surface", ["owner", "shared"])
def test_compiler_consumes_same_immutable_snapshot_as_provenance(disposable_project, tmp_path, monkeypatch, surface):
    root, generator, source = copy_project(disposable_project, tmp_path)
    before_source = inventory(source)
    compile_actual = generator._compile
    raced = []
    def interpose(*args, **kwargs):
        if surface == "owner":
            p = source / "main.tsp"
            p.write_text(p.read_text().replace("label: string;", "changed_after_snapshot: string;"))
        else:
            p = root / "contracts/primitives/main.tsp"
            p.write_text(p.read_text().replace("scalar NonnegativeInt64Decimal extends string;", "@maxLength(1)\nscalar NonnegativeInt64Decimal extends string;"))
        raced.append(True)
        return compile_actual(*args, **kwargs)
    monkeypatch.setattr(generator, "_compile", interpose)
    out = root / "snapshot"
    assert invoke(generator, out, source) == 0
    assert raced, "real_compile_boundary_not_exercised"
    contract = bundle(out)
    assert contract.validate_request("submit", {"revision":"12","enabled":True,"nested":{"label":"x"}})
    # Compare whole provenance with a clean same-root generation: absolute paths are not provenance.
    assert contract.manifest["provenance"] == bundle(disposable_project / "baseline").manifest["provenance"]
    if surface == "owner": assert inventory(source) != before_source


@pytest.mark.parametrize("mutation", ["node_lock", "package_declaration", "python_lock", "python_declaration", "source_pin", "missing_node", "missing_python", "missing_python_stdlib"])
def test_inconsistent_tool_declarations_and_missing_artifact_sets_refuse(disposable_project, tmp_path, mutation):
    root, generator, source = copy_project(disposable_project, tmp_path)
    tools = root / "tools/contracts"
    if mutation in ("node_lock", "package_declaration", "source_pin"):
        file = {"node_lock":"package-lock.json", "package_declaration":"package.json", "source_pin":"toolchain/source-pins.json"}[mutation]
        p = tools / file; value = json.loads(p.read_bytes())
        if mutation == "node_lock": value["packages"]["node_modules/@typespec/compiler"]["version"] = "0.0.0"
        if mutation == "package_declaration": value["dependencies"]["@typespec/compiler"] = "0.0.0"
        if mutation == "source_pin": value["versions"]["node"] = "0.0.0"
        p.write_text(json.dumps(value))
    elif mutation.startswith("missing_"):
        p = tools / "toolchain/tool-artifacts.json";value = json.loads(p.read_bytes());del value["sets"][mutation.removeprefix("missing_")];p.write_text(json.dumps(value))
    else:
        p = tools / ("uv.lock" if mutation == "python_lock" else "pyproject.toml")
        text = p.read_text()
        old, new = ('name = "openapi-python-client"\nversion = "0.29.1"', 'name = "openapi-python-client"\nversion = "0.0.0"') if mutation == "python_lock" else ("openapi-python-client==0.29.1", "openapi-python-client==0.0.0")
        assert text.count(old) == 1
        p.write_text(text.replace(old, new))
    before = inventory(source)
    with pytest.raises(ContractError): invoke(generator, root / "must-not-exist", source)
    assert not (root / "must-not-exist").exists() and inventory(source) == before


def test_ambient_python_startup_never_executes(real_projects, tmp_path, monkeypatch):
    real_projects("base")  # real qualified baseline before adversarial startup setup
    sentinel = tmp_path / "startup-executed"
    startup = tmp_path / "ambient"; startup.mkdir()
    (startup / "sitecustomize.py").write_text("from pathlib import Path\nPath(" + repr(str(sentinel)) + ").write_text('unqualified-startup')\n")
    monkeypatch.setenv("PYTHONPATH", str(startup))
    out = tmp_path / "output"
    assert invoke(load_generator(), out) == 0
    assert not sentinel.exists(), "ambient_startup_executed_before_qualification"
    bundle(out)


@pytest.mark.parametrize("surface", ["absolute_output", "relative_output", "output_name", "unknown_emitter", "local_js", "escaping_tsp", "absolute_tsp"])
def test_unqualified_compiler_surface_refuses_before_execution(disposable_project, tmp_path, monkeypatch, surface):
    root, generator, source = copy_project(disposable_project, tmp_path)
    sentinel = tmp_path / "UNOWNED_EXECUTION_SENTINEL"
    unowned = tmp_path / "unowned"
    config = source / "tspconfig.yaml"
    text = config.read_text()
    if surface == "absolute_output": text = text.replace('  "@typespec/openapi3":', '  "@typespec/openapi3":\n    emitter-output-dir: ' + json.dumps(str(unowned)))
    if surface == "relative_output": text = text.replace('  "@typespec/openapi3":', '  "@typespec/openapi3":\n    emitter-output-dir: "../../unowned"')
    if surface == "output_name": text = text.replace('output-file: "contract.openapi.json"', 'output-file: ' + json.dumps(str(unowned / "escaped.openapi.json")))
    if surface == "unknown_emitter": text = text.replace('  - "@typespec/openapi3"', '  - "PRIVATE_UNQUALIFIED_EMITTER"')
    config.write_text(text)
    if surface == "local_js":
        (source / "plugin.js").write_text("import {writeFileSync} from 'node:fs';writeFileSync(" + json.dumps(str(sentinel)) + ",'executed');\n")
        p = source / "main.tsp";p.write_text('import "./plugin.js";\n' + p.read_text())
    if surface in ("escaping_tsp", "absolute_tsp"):
        outside = root / "tools/contracts/fixtures/outside.tsp";outside.write_text("namespace Outside; model Data {value:string;}\n")
        imported = "../outside.tsp" if surface == "escaping_tsp" else str(outside)
        p = source / "main.tsp";p.write_text("import " + json.dumps(imported) + ";\n" + p.read_text())
    before = inventory(source)
    calls = []
    actual_run = subprocess.run
    def observe(command, *args, **kwargs):
        if any(str(part).endswith("/cmd/tsp.js") for part in command): calls.append(list(command))
        return actual_run(command, *args, **kwargs)
    monkeypatch.setattr(subprocess, "run", observe)
    try:
        with pytest.raises(ContractError): invoke(generator, root / "refused", source)
    finally:
        assert not sentinel.exists(), "owner_javascript_executed"
        assert not unowned.exists(), "emitter_escaped_owned_scratch"
        assert inventory(source) == before, "compiler_changed_authored_source"
    assert not calls, "unqualified_config_or_import_reached_real_compiler"
    assert not (root / "refused").exists()


def test_qualified_local_typespec_import_remains_supported(disposable_project, tmp_path):
    root, generator, source = copy_project(disposable_project, tmp_path)
    p = source / "main.tsp"
    p.write_text('import "./nested.tsp";\n' + p.read_text().replace("  model Nested { label: string; }", ""))
    (source / "nested.tsp").write_text("namespace Probe; model Nested { label: string; }\n")
    before = inventory(source)
    out = root / "local-import"
    assert invoke(generator, out, source) == 0 and inventory(source) == before
    assert bundle(out).validate_request("submit", {"revision":"12","enabled":True,"nested":{"label":"x"}})


def test_failed_atomic_replacement_restores_exact_tree(real_projects, tmp_path, monkeypatch):
    baseline = real_projects("base")
    source = tmp_path / "source";shutil.copytree(SOURCE, source)
    out = tmp_path / "out";shutil.copytree(baseline, out)
    p = source / "main.tsp";p.write_text(p.read_text().replace("label: string;", "renamed: string;"))
    before_output, before_source = inventory(out), inventory(source)
    replace = os.replace
    failures = []
    def fail_candidate(src, dst):
        if Path(dst) == out and not failures:
            failures.append(True)
            raise OSError("private_simulated_replacement_failure")
        return replace(src, dst)
    monkeypatch.setattr(os, "replace", fail_candidate)
    with pytest.raises(ContractError): invoke(load_generator(), out, source)
    assert failures, "replacement_failure_not_exercised"
    assert inventory(out) == before_output and inventory(source) == before_source


def test_python_valid_percent_path_encodes_and_decodes_once(real_projects):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(ROOT / "packages/vuoro-contracts/src"))
    observed = run([executable("VUORO_CONTRACT_PYTHON"), str(HELPERS / "python_sdk_regressions.py"), str(real_projects("base")), "percent_path"], env)
    assert json.loads(observed) == {"scenario":"percent_path", "result":"pass"}


def test_typescript_legal_underscore_identity_binds_actual_native_export(real_projects, tmp_path):
    oracle = build_ts_regression(real_projects("underscore"), tmp_path)
    observed = run([executable("VUORO_CONTRACT_NODE"), str(oracle), "underscore_operation"])
    assert json.loads(observed) == {"scenario":"underscore_operation", "result":"pass"}


@pytest.mark.parametrize("layout", ["same_line", "comment_prefix"])
def test_every_owner_import_is_admitted_before_real_compiler(disposable_project, tmp_path, monkeypatch, layout):
    # disposable_project has already completed an actual clean qualified generation.
    root, generator, source = copy_project(disposable_project, tmp_path)
    sentinel = tmp_path / "OUTSIDE_OWNER_JS_EXECUTED"
    outside = tmp_path / "outside-owner.js"
    outside.write_text("require('node:fs').writeFileSync(" + json.dumps(str(sentinel)) + ", 'executed');\n")
    imported = "import " + json.dumps(str(outside)) + ";"
    prefix = 'import "@typespec/http"; ' if layout == "same_line" else "/* legal leading TypeSpec comment */ "
    main = source / "main.tsp"
    main.write_text(prefix + imported + "\n" + main.read_text())
    before = inventory(source)
    calls = []
    actual_run = subprocess.run
    def observe(command, *args, **kwargs):
        if any(str(part).endswith("/cmd/tsp.js") for part in command): calls.append(list(command))
        return actual_run(command, *args, **kwargs)
    monkeypatch.setattr(subprocess, "run", observe)
    try:
        with pytest.raises(ContractError): invoke(generator, root / "must-refuse", source)
    finally:
        assert not sentinel.exists(), "unqualified_owner_import_executed_javascript"
        assert inventory(source) == before, "compiler_changed_original_owner_source"
    assert not calls, "unqualified_owner_import_reached_actual_compiler"
    assert not (root / "must-refuse").exists()


@pytest.fixture(scope="module")
def final_parameter_projects(real_projects, tmp_path_factory):
    """Extend real producer variants without changing any prior oracle body."""
    work = tmp_path_factory.mktemp("f1b-final-parameter-projects")
    cache = {"base":real_projects("base"), "query":real_projects("query")}
    def variant(kind):
        if kind not in cache:
            source = work / (kind + "-source")
            shutil.copytree(SOURCE, source)
            p = source / "main.tsp"
            text = p.read_text()
            if kind == "optional_header":
                text = text.replace("op submit(@path key: Key, @body input: Input)", 'op submit(@path key: Key, @body input: Input, @header("x-token") token?: Key)')
            elif kind == "numeric":
                text = text.replace("model Ok is JsonResponse<200, Result>;", "@minValue(0) @maxValue(100) scalar Count extends int32;\n  model Ok is JsonResponse<200, Result>;")
                text = text.replace("op submit(@path key: Key, @body input: Input)", "op submit(@path key: Key, @body input: Input, @query count?: Count)")
            else:
                raise AssertionError("unknown_final_parameter_variant")
            assert text != p.read_text()
            p.write_text(text)
            out = work / kind
            assert invoke(load_generator(), out, source) == 0
            contract = bundle(out)
            if kind == "numeric":
                assert contract.validate_parameter("submit", "query", "count", 7) == 7
                with pytest.raises(ContractError): contract.validate_parameter("submit", "query", "count", 101)
                with pytest.raises(ContractError): contract.validate_parameter("submit", "query", "count", "7")
            cache[kind] = out
        return cache[kind]
    return variant


@pytest.mark.parametrize("scenario,kind", [("omitted_query","query"), ("omitted_header","optional_header"), ("wrong_origin","base"), ("numeric_query","numeric")])
def test_python_serialized_presence_origin_and_numeric_parameters(final_parameter_projects, scenario, kind):
    output = final_parameter_projects(kind)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(ROOT / "packages/vuoro-contracts/src"))
    # run_name avoids the existing independent oracle's CLI while reusing its imports.
    program = "import json,runpy,sys;from pathlib import Path;m=runpy.run_path(sys.argv[1],run_name='final_oracle');print(json.dumps(m['exercise_final_parameter_regression'](Path(sys.argv[2]),sys.argv[3])))"
    observed = run([executable("VUORO_CONTRACT_PYTHON"), "-c", program, str(HELPERS / "python_sdk_regressions.py"), str(output), scenario], env)
    assert json.loads(observed) == {"scenario":scenario, "result":"pass"}


def build_ts_presence_regression(output, work):
    shutil.copytree(output / "clients/typescript", work / "client")
    path = work / "client/client/client.gen.ts"
    text = path.read_text()
    anchor = "request = new Request(url, requestInit);"
    assert text.count(anchor) == 1, "qualified_native_request_anchor_changed"
    text = text.replace(anchor, """const presenceUrl = new URL(url);
if ((globalThis as any).__presenceMutation === 'query') presenceUrl.searchParams.delete('selector');
request = new Request(presenceUrl, requestInit);
if ((globalThis as any).__presenceMutation === 'header') request.headers.delete('x-token');""")
    serializer = "opts.serializedBody = opts.bodySerializer(opts.body) as string | undefined;"
    assert text.count(serializer) == 1, "qualified_native_serializer_anchor_changed"
    text = text.replace(serializer, "(globalThis as any).__presenceSerializers = ((globalThis as any).__presenceSerializers ?? 0) + 1;\n" + serializer)
    entry_anchor = "const request: Client['request'] = async (options) => {"
    assert text.count(entry_anchor) == 1, "qualified_native_entry_anchor_changed"
    text = text.replace(entry_anchor, entry_anchor + "\n(globalThis as any).__presenceEntries = ((globalThis as any).__presenceEntries ?? 0) + 1;")
    path.write_text(text)
    runner = r'''import assert from 'node:assert/strict';
import {submit} from '__SDK__';
import {createClient} from '__CLIENT__';
import {createValidatedClient} from '__ATTACHMENT__';
import {ContractError} from '__DECODER__';
const location=process.argv[2],success={state:'ready',revision:'12'},wires:any[]=[];
(globalThis as any).fetch=async()=>{throw new Error('regression_network_forbidden')};
const fetcher:any=async(req:Request)=>{assert(req instanceof Request);wires.push({url:req.url,token:req.headers.get('x-token')});return new Response(JSON.stringify(success),{status:200,headers:{'Content-Type':'application/json'}})};
const options:any={path:{key:'k'},body:{revision:'12',enabled:true,nested:{label:'x'}}};
if(location==='query')options.query={selector:'k'};else if(location==='header')options.headers={'x-token':'k'};else throw new Error('unknown_presence_scenario');
assert.deepEqual((await submit({...options,client:createClient({baseUrl:'https://contracts.invalid',fetch:fetcher}),throwOnError:true})).data,success);
if(location==='query')assert(wires.at(-1).url.endsWith('?selector=k'));else assert.equal(wires.at(-1).token,'k');
const guarded=createValidatedClient({baseUrl:'https://contracts.invalid',fetch:fetcher});
assert.deepEqual((await guarded.submit({path:options.path,body:options.body})).body,success);
assert.equal(wires.at(-1).url,'https://contracts.invalid/probe/k');assert.equal(wires.at(-1).token,null);
assert.deepEqual((await guarded.submit(options)).body,success);
if(location==='query')assert(wires.at(-1).url.endsWith('?selector=k'));else assert.equal(wires.at(-1).token,'k');
assert.equal(wires.length,3);assert.equal((globalThis as any).__presenceEntries,3);assert.equal((globalThis as any).__presenceSerializers,3);
(globalThis as any).__presenceMutation=location;
await assert.rejects(()=>guarded.submit(options),(e:any)=>{assert(e instanceof ContractError,'unsafe_native_error');assert(!JSON.stringify({message:e.message,...e,stack:e.stack}).includes('PRIVATE_'));return true;});
assert.equal(wires.length,3,'missing_supplied_parameter_reached_transport');assert.equal((globalThis as any).__presenceEntries,4);assert.equal((globalThis as any).__presenceSerializers,4);
console.log(JSON.stringify({scenario:location,result:'pass'}));'''
    replacements = {"__SDK__":str(work / "client/sdk.gen.ts"), "__CLIENT__":str(work / "client/client/index.ts"), "__ATTACHMENT__":str(work / "client/validated-sdk.ts"), "__DECODER__":str(work / "client/strict-json.ts")}
    for key, value in replacements.items(): runner = runner.replace(key, value)
    entry = work / "presence.ts"
    entry.write_text(runner)
    program = "import {pathToFileURL} from 'node:url';const [api,entry,out,mods]=process.argv.slice(1);const {build}=await import(pathToFileURL(api).href);await build({entryPoints:[entry],outfile:out,bundle:true,platform:'node',format:'esm',target:'node24',nodePaths:[mods],logLevel:'error'});"
    out = work / "presence.mjs"
    run([executable("VUORO_CONTRACT_NODE"), "--input-type=module", "-e", program, str(TOOLS / "node_modules/esbuild/lib/main.js"), str(entry), str(out), str(TOOLS / "node_modules")])
    return out


@pytest.mark.parametrize("location,kind", [("query","query"), ("header","optional_header")])
def test_typescript_supplied_optional_parameter_cannot_disappear(final_parameter_projects, tmp_path, location, kind):
    oracle = build_ts_presence_regression(final_parameter_projects(kind), tmp_path)
    observed = run([executable("VUORO_CONTRACT_NODE"), str(oracle), location])
    assert json.loads(observed) == {"scenario":location, "result":"pass"}
