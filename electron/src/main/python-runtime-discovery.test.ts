// @vitest-environment node
import { mkdtemp, mkdir, symlink, writeFile, rm, readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve, win32 } from 'node:path';
import { afterEach, describe, expect, test } from 'vitest';
import { discoverPythonRuntimes, type PythonDiscoveryFileSystem } from './python-runtime-discovery';
const cleanups: string[] = [];
afterEach(async () => { await Promise.all(cleanups.splice(0).map(p => rm(p, { recursive: true, force: true }))); });
async function fixture() { const root = await mkdtemp(join(tmpdir(), 'python-discovery-')); cleanups.push(root); return root; }
async function makeVenv(root: string, name: string, version = '3.12.1') {
  const env = join(root, name); await mkdir(join(env, 'bin'), { recursive: true });
  await writeFile(join(env, 'bin', 'python'), 'not executed');
  await writeFile(join(env, 'pyvenv.cfg'), `home = /base\nversion = ${version}\n`); return env;
}
const fakeFs = (overrides: Partial<PythonDiscoveryFileSystem> = {}): PythonDiscoveryFileSystem => ({
  readdir: async () => [], stat: async () => ({ isFile: () => false, isDirectory: () => true }),
  lstat: async () => ({ isSymbolicLink: () => false, isDirectory: () => true }), realpath: async p => p,
  readFile: async () => { throw Object.assign(new Error('missing'), {code:'ENOENT'}); }, ...overrides,
});
describe('discoverPythonRuntimes', () => {
  test('finds project venv metadata without executing the interpreter', async () => {
    const root = await fixture(), env = await makeVenv(root, 'project/.venv');
    const result = await discoverPythonRuntimes({ roots: [root], homeDir: root, env: { PATH: '' } });
    expect(result.candidates).toContainEqual({ pythonPath: join(env, 'bin', 'python'), source: 'venv', version: '3.12.1' });
    expect(await readFile(join(env,'bin','python'),'utf8')).toBe('not executed');
  });
  test('deduplicates command aliases', async () => {
    const root = await fixture(), bin = join(root,'bin'); await mkdir(bin);
    await writeFile(join(bin,'python'),'not executed'); await symlink(join(bin,'python'),join(bin,'python3'));
    const result = await discoverPythonRuntimes({roots:[],homeDir:root,env:{PATH:bin}});
    expect(result.candidates).toHaveLength(1);
  });
  test('preserves separate PATH virtualenvs sharing one base Python and deduplicates each environment', async () => {
    const root = await fixture(), a = await makeVenv(root,'a/.venv'), b = await makeVenv(root,'b/.venv');
    const base=join(root,'base');await writeFile(base,'not executed');
    for(const env of [a,b]) {await rm(join(env,'bin','python'));await symlink(base,join(env,'bin','python'));await symlink(base,join(env,'bin','python3'));}
    const result=await discoverPythonRuntimes({roots:[root],homeDir:root,env:{PATH:[join(a,'bin'),join(b,'bin')].join(':')}});
    expect(result.candidates.map(x=>x.pythonPath)).toEqual([join(a,'bin','python'),join(b,'bin','python')]);
  });
  test('reports EACCES and EPERM as partial, preserving readable results', async () => {
    const root=await fixture();await makeVenv(root,'project/.venv');
    for(const code of ['EACCES','EPERM']) {
      const result=await discoverPythonRuntimes({roots:[root,'/denied'],homeDir:root,env:{PATH:''},fs:{lstat:async p=>{if(p==='/denied')throw Object.assign(new Error('denied'),{code});return (await import('node:fs/promises')).lstat(p);}}});
      expect(result.candidates.length).toBeGreaterThan(0);expect(result.partial).toBe(true);
      expect(result.warnings).toContainEqual({path:'/denied',reason:'permission_denied'});
    }
  });
  test('does not label absent common locations as errors',async()=>{
    const root=await fixture();const result=await discoverPythonRuntimes({roots:[join(root,'absent')],homeDir:root,env:{PATH:''}});
    expect(result).toMatchObject({candidates:[],warnings:[],partial:false});
  });
  test('reports an actual directory budget cutoff',async()=>{
    const result=await discoverPythonRuntimes({roots:['/fixture'],homeDir:'/fixture',env:{PATH:''},maxDirs:1,fs:fakeFs({readdir:async p=>p==='/fixture'?['child']:[]})});
    expect(result.partial).toBe(true);expect(result.warnings.some(w=>w.reason==='scan_limit')).toBe(true);
  });
  test('reports other filesystem failures as incomplete rather than silently empty',async()=>{
    const result=await discoverPythonRuntimes({roots:['/bad'],env:{PATH:''},fs:fakeFs({lstat:async()=>{throw Object.assign(new Error('io'),{code:'EIO'});}})});
    expect(result.partial).toBe(true);expect(result.warnings).toContainEqual({path:'/bad',reason:'read_error'});
  });
  test('supports Windows Scripts paths on a non-Windows test host',async()=>{
    const root=win32.join('C:\\','Work','app'),venv=win32.join(root,'venv'),exe=win32.join(venv,'Scripts','python.exe');
    const dirs=new Set([root,venv,win32.dirname(exe)]),cfg=win32.join(venv,'pyvenv.cfg');
    const missing=()=>{throw Object.assign(new Error('missing'),{code:'ENOENT'});};
    const fs=fakeFs({
      stat:async p=>p===exe?{isFile:()=>true,isDirectory:()=>false}:dirs.has(p)?{isFile:()=>false,isDirectory:()=>true}:missing(),
      lstat:async p=>dirs.has(p)?{isSymbolicLink:()=>false,isDirectory:()=>true}:missing(),
      readdir:async p=>p===root?['venv']:p===win32.dirname(exe)?['python.exe']:dirs.has(p)?[]:missing(),
      readFile:async p=>p===cfg?'version = 3.14.1':missing(),
    });
    const result=await discoverPythonRuntimes({platform:'win32',homeDir:win32.join('C:\\','Users','tester'),roots:[root],env:{PATH:''},fs});
    expect(result.candidates).toEqual([{pythonPath:exe,source:'venv',version:'3.14.1'}]);
  });
  test('returns partial on a slow operation even if it is the final configured directory',async()=>{
    const result=await discoverPythonRuntimes({roots:['/slow'],homeDir:'/fixture',env:{PATH:''},timeoutMs:15,fs:fakeFs({readdir:async()=>new Promise<string[]>(()=>{})})});
    expect(result.partial).toBe(true);expect(result.warnings.some(w=>w.reason==='scan_limit')).toBe(true);
  });
  test('never follows directory symlink cycles or descends node_modules',async()=>{
    const root=await fixture();await makeVenv(root,'project/.venv');await mkdir(join(root,'node_modules','ignored'),{recursive:true});
    await symlink(root,join(root,'project','loop'));
    const result=await discoverPythonRuntimes({roots:[root],homeDir:root,env:{PATH:''},maxDepth:2});
    expect(result.candidates).toHaveLength(1);expect(result.partial).toBe(false);
  });
  test('discovers arbitrary pyenv release names without version hardcoding',async()=>{
    const home=await fixture(),root=join(home,'.pyenv','versions'),env=await makeVenv(root,'3.14.7');
    const result=await discoverPythonRuntimes({roots:[root],homeDir:home,env:{PATH:''}});
    expect(result.candidates).toContainEqual({pythonPath:join(env,'bin','python'),source:'pyenv',version:'3.12.1'});
  });
  test('has no subprocess execution dependency',async()=>{
    const source=await readFile(resolve(__dirname,'python-runtime-discovery.ts'),'utf8');
    expect(source).not.toMatch(/child_process|\bspawn\s*\(|\bexec(?:File|Sync)?\s*\(/);
  });
});
