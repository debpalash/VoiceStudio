// @vitest-environment node
import { afterAll, beforeAll, expect, it } from 'vitest';
import { build } from 'vite';
import { execFileSync, spawnSync } from 'node:child_process';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';

let directory: string;
beforeAll(async () => {
  directory = await mkdtemp(join(tmpdir(), 'voice-real-rejection-'));
  const entry = join(directory, 'entry.ts');
  const modulePath = (name: string) => JSON.stringify(resolve(__dirname, name));
  await writeFile(
    entry,
    [
      `export { DiagnosticReports } from ${modulePath('diagnostics.ts')};`,
      `export { installDiagnosticProcessCapture } from ${modulePath('diagnostics-capture.ts')};`,
      `export { runDiagnosticOperation, diagnosticOperationOwner } from ${modulePath('diagnostic-operation.ts')};`,
    ].join('\n'),
  );
  await build({
    configFile: false,
    logLevel: 'silent',
    build: {
      ssr: entry,
      outDir: directory,
      emptyOutDir: false,
      rollupOptions: { output: { entryFileNames: 'capture.mjs' } },
    },
  });
  await writeFile(
    join(directory, 'probe.mjs'),
    String.raw`
    import { DiagnosticReports, installDiagnosticProcessCapture, runDiagnosticOperation } from './capture.mjs';
    import { mkdtemp, rm } from 'node:fs/promises';
    import { join } from 'node:path';
    import { tmpdir } from 'node:os';
    const directory = await mkdtemp(join(tmpdir(), 'voice-rejection-data-'));
    const reports = new DiagnosticReports({
      directory, identity: { app_version: '1.0.0', build_commit: '0000000', platform: 'linux', arch: 'x64' },
      automatic: false, env: {}, destination: { site: 'fixture', host: 'https://fixture.invalid', appUrl: 'https://fixture.invalid' },
    });
    await reports.initialize();
    const stop = installDiagnosticProcessCapture(reports);
    const [control, mode] = process.argv[2].split(':');
    if (control !== 'off-to-on' && control !== 'unknown-off-to-on') await reports.setConsent(true);
    let release;
    const held = new Promise(resolve => { release = resolve; });
    const action = async () => {
      await held;
      // A nested Promise after the grant changes still belongs to the old unit.
      await Promise.resolve();
      const fail = () => { throw Object.assign(new Error('PRIVATE_TEXT'), { name: control === 'cancel' ? 'AbortError' : 'Error' }); };
      if (mode === 'detached') { void Promise.resolve().then(fail); return; }
      if (mode === 'timer') { setTimeout(fail, 0); return; }
      fail();
    };
    if (control.startsWith('unknown')) action();
    else runDiagnosticOperation(reports, action);
    if (control === 'off-to-on' || control === 'unknown-off-to-on') await reports.setConsent(true);
    if (control === 'a-to-b' || control === 'unknown-a-to-b') { await reports.setConsent(false); await reports.setConsent(true); }
    if (control === 'on-to-off') await reports.setConsent(false);
    // This fixture-only fatal listener lets the child inspect the monitor's
    // result. Production capture only observes and never changes fatal policy.
    const delivered = new Promise(resolve => process.once(mode === 'timer' ? 'uncaughtException' : 'unhandledRejection', () => setImmediate(resolve)));
    release(); await delivered;
    const pending = reports.status().pending_count;
    // A separate later action under the current grant is eligible.
    let freshPending = pending;
    if (['off-to-on', 'a-to-b', 'unknown-off-to-on', 'unknown-a-to-b'].includes(control)) {
      const next = new Promise(resolve => process.once('unhandledRejection', () => setImmediate(resolve)));
      runDiagnosticOperation(reports, async () => { throw new Error('PRIVATE_FRESH'); });
      await next; freshPending = reports.status().pending_count;
    }
    console.log(JSON.stringify({ pending, freshPending }));
    stop(); await reports.close(); await rm(directory, { recursive: true, force: true });
  `,
  );
});
beforeAll(async () => {
  await writeFile(
    join(directory, 'handled.mjs'),
    String.raw`
    import assert from 'node:assert/strict';
    import { DiagnosticReports, installDiagnosticProcessCapture, runDiagnosticOperation } from './capture.mjs';
    import { mkdtemp, rm } from 'node:fs/promises'; import { tmpdir } from 'node:os'; import { join } from 'node:path';
    const directory = await mkdtemp(join(tmpdir(), 'voice-handled-data-'));
    const reports = new DiagnosticReports({ directory, identity: { app_version: '1.0.0', build_commit: '0000000', platform: 'linux', arch: 'x64' }, automatic: false, env: {}, destination: { site: 'fixture', host: 'https://fixture.invalid', appUrl: 'https://fixture.invalid' } });
    await reports.initialize(); await reports.setConsent(true); const stop = installDiagnosticProcessCapture(reports);
    const error = Object.freeze(new Error('fixture')); const keys = Object.getOwnPropertyNames(error);
    await assert.rejects(runDiagnosticOperation(reports, () => Promise.reject(error)), (value) => value === error);
    assert.deepEqual(Object.getOwnPropertyNames(error), keys);
    assert.equal(await runDiagnosticOperation(reports, async () => 42), 42);
    const object = {}; assert.equal(runDiagnosticOperation(reports, () => object), object);
    const unknown = Promise.resolve(42); assert.equal(runDiagnosticOperation(reports, () => unknown), unknown);
    let getterCalls=0, original;
    const customized = runDiagnosticOperation(reports, () => { original=Promise.resolve(42); Object.defineProperty(original,'then',{get(){getterCalls++;return Promise.prototype.then;}});return original; });
    assert.equal(customized,original);assert.equal(getterCalls,0);assert.equal(await customized.then(value=>value),42);assert.equal(getterCalls,1);
    let proxyReads=0; const proxy=new Proxy(Promise.resolve(42),{getPrototypeOf(){proxyReads++;throw new Error('fixture');}});
    assert.equal(runDiagnosticOperation(reports,()=>proxy),proxy);assert.equal(proxyReads,0);
    let reference;
    await (async () => { const fault = new Error('fixture'); reference = new WeakRef(fault); await runDiagnosticOperation(reports, () => Promise.reject(fault)).catch(() => {}); })();
    for (let attempt=0; attempt<30; attempt++) { await new Promise(setImmediate); global.gc(); await new Promise(setImmediate); if (!reference.deref()) break; }
    assert.equal(reference.deref(), undefined, 'fault authority retained the Error');
    let parentReference;
    await (async()=>{const parent=runDiagnosticOperation(reports,()=>Promise.resolve(42));parentReference=new WeakRef(parent);await runDiagnosticOperation(reports,async()=>await parent);})();
    for(let attempt=0;attempt<30;attempt++){await new Promise(setImmediate);global.gc();await new Promise(setImmediate);if(!parentReference.deref())break;}
    assert.equal(parentReference.deref(),undefined,'parent observation retained the Promise');
    await new Promise(setImmediate); assert.equal(reports.status().pending_count, 0);
    console.log(JSON.stringify({ handled: true, retained: false }));
    stop(); await reports.close(); await rm(directory, { recursive: true, force: true });
  `,
  );
  await writeFile(
    join(directory, 'derived.mjs'),
    String.raw`
    import { DiagnosticReports, installDiagnosticProcessCapture, runDiagnosticOperation } from './capture.mjs';
    import { mkdtemp, rm } from 'node:fs/promises';
    import { join } from 'node:path'; import { tmpdir } from 'node:os';
    const directory = await mkdtemp(join(tmpdir(), 'voice-derived-data-'));
    const reports = new DiagnosticReports({ directory, identity: { app_version: '1.0.0', build_commit: '0000000', platform: 'linux', arch: 'x64' }, automatic: false, env: {}, destination: { site: 'fixture', host: 'https://fixture.invalid', appUrl: 'https://fixture.invalid' } });
    await reports.initialize(); const stop = installDiagnosticProcessCapture(reports);
    const [transition, mode] = process.argv[2].split(':');
    if (transition !== 'off-on') await reports.setConsent(true);
    let resolve, reject; const make = () => new Promise((yes,no) => {resolve=yes;reject=no;});
    const original = (mode.startsWith('unknown') || mode.startsWith('fresh-unknown') || mode === 'catch-new') ? make() : runDiagnosticOperation(reports, make);
    const error = new Error('PRIVATE_ORIGINAL');
    if (mode === 'same-error') { reject(error); await original.catch(() => {}); }
    if (transition !== 'same-grant') { await reports.setConsent(false); await reports.setConsent(true); }
    let unhandled=0, warnings=0, strictFatals=0, firstPromise, final, freshPromise, freshMatched=false;
    if(process.execArgv.includes('--unhandled-rejections=strict')) process.on('uncaughtException',(_error,origin)=>{if(origin!=='unhandledRejection')process.exit(91);strictFatals++;});
    process.on('unhandledRejection', () => unhandled++);
    process.on('warning', warning => { if (warning.name === 'PromiseRejectionHandledWarning') warnings++; });
    const delivered = new Promise(resolve => process.once('unhandledRejection', (_error,promise) => { firstPromise=promise;setImmediate(resolve); }));
    if (mode === 'same-error') final = runDiagnosticOperation(reports, async () => { throw error; });
    else if (mode.includes('await')) final = runDiagnosticOperation(reports, async () => { await original; if (mode.startsWith('fresh')) throw new Error('PRIVATE_NEW'); });
    else if (mode === 'return-known' || mode === 'unknown-native') final = runDiagnosticOperation(reports, () => original);
    else if (mode === 'catch-new') final = runDiagnosticOperation(reports, () => original.catch(() => { throw new Error('PRIVATE_NEW'); }));
    else final = runDiagnosticOperation(reports, () => original.then(() => { if (mode.startsWith('fresh')) throw new Error('PRIVATE_NEW'); }));
    if (mode.startsWith('fresh')) resolve('PRIVATE_OLD_DATA'); else if (mode !== 'same-error') reject(mode === 'primitive' ? 'PRIVATE_PRIMITIVE' : error);
    await delivered; const pending = reports.status().pending_count;
    const next = new Promise(resolve => process.once('unhandledRejection', (_error,promise) => {freshMatched=promise===freshPromise;setImmediate(resolve);}));
    freshPromise = runDiagnosticOperation(reports, async () => { throw new TypeError('PRIVATE_FRESH_ACTION'); });
    await next; await new Promise(setImmediate);console.log(JSON.stringify({ pending, freshPending: reports.status().pending_count, unhandled, warnings, strictFatals, firstMatches:firstPromise===final, freshMatched }));
    stop(); await reports.close(); await rm(directory, { recursive: true, force: true });
  `,
  );
});
beforeAll(async () => {
  await writeFile(
    join(directory, 'guards.mjs'),
    String.raw`
    import assert from 'node:assert/strict';import {runInNewContext} from 'node:vm';
    import { DiagnosticReports, installDiagnosticProcessCapture, runDiagnosticOperation } from './capture.mjs';
    import { mkdtemp, rm } from 'node:fs/promises'; import { tmpdir } from 'node:os'; import { join } from 'node:path';
    const directory=await mkdtemp(join(tmpdir(),'voice-promise-guards-'));
    const reports=new DiagnosticReports({directory,identity:{app_version:'1.0.0',build_commit:'0000000',platform:'linux',arch:'x64'},automatic:false,env:{},destination:{site:'fixture',host:'https://fixture.invalid',appUrl:'https://fixture.invalid'}});
    await reports.initialize();await reports.setConsent(true);const stop=installDiagnosticProcessCapture(reports);
    const mode=process.argv[2];let reject,calls=0,restore=()=>{};
    let original=new Promise((_,no)=>{reject=no;});
    if(mode==='own-constructor')Object.defineProperty(original,'constructor',{get(){calls++;return Promise;}});
    if(mode==='own-then')Object.defineProperty(original,'then',{get(){calls++;return Promise.prototype.then;}});
    if(mode==='prototype-constructor'){
      const descriptor=Object.getOwnPropertyDescriptor(Promise.prototype,'constructor');restore=()=>Object.defineProperty(Promise.prototype,'constructor',descriptor);
      Object.defineProperty(Promise.prototype,'constructor',{configurable:true,get(){calls++;return Promise;}});
    }
    if(mode==='species-getter'||mode==='custom-species'){
      const descriptor=Object.getOwnPropertyDescriptor(Promise,Symbol.species);restore=()=>Object.defineProperty(Promise,Symbol.species,descriptor);
      if(mode==='species-getter')Object.defineProperty(Promise,Symbol.species,{configurable:true,get(){calls++;return Promise;}});
      else Object.defineProperty(Promise,Symbol.species,{configurable:true,value:class extends Promise{constructor(...args){super(...args);calls++;}}});
    }
    if(mode==='foreign'){
      const foreign=runInNewContext('(()=>{let reject;const promise=new Promise((_,no)=>reject=no);return {promise,reject};})()');original=foreign.promise;reject=foreign.reject;
    }
    let unhandled=0,warnings=0,seen;
    process.on('unhandledRejection',()=>unhandled++);process.on('warning',warning=>{if(warning.name==='PromiseRejectionHandledWarning')warnings++;});
    const delivered=new Promise(resolve=>process.once('unhandledRejection',(_error,promise)=>{seen=promise;setImmediate(resolve);}));
    let final;try{final=runDiagnosticOperation(reports,()=>original.then());}finally{restore();}
    reject(new Error('fixture'));await delivered;await new Promise(setImmediate);
    assert.equal(calls,mode==='foreign'?0:1);assert.equal(unhandled,1);assert.equal(warnings,0);assert.equal(seen,final);assert.equal(reports.status().pending_count,0);
    console.log(JSON.stringify({calls,unhandled,warnings,local:true}));stop();await reports.close();await rm(directory,{recursive:true,force:true});
  `,
  );
});
afterAll(async () => {
  if (directory) await rm(directory, { recursive: true, force: true });
});

it.each(
  [
    'off-to-on',
    'a-to-b',
    'same-grant',
    'unknown',
    'unknown-off-to-on',
    'unknown-a-to-b',
    'cancel',
    'on-to-off',
  ].flatMap((control) => [control, `${control}:detached`, `${control}:timer`]),
)('uses the originating operation for actual Node rejection and timer failures: %s', (control) => {
  const kind = control.split(':')[0];
  const captured = kind === 'same-grant';
  const output = execFileSync(process.execPath, [join(directory, 'probe.mjs'), control], {
    encoding: 'utf8',
    timeout: 15_000,
  });
  expect(JSON.parse(output)).toEqual({
    pending: captured ? 1 : 0,
    freshPending:
      captured || ['off-to-on', 'a-to-b', 'unknown-off-to-on', 'unknown-a-to-b'].includes(kind)
        ? 1
        : 0,
  });
  expect(output).not.toContain('PRIVATE_');
});

it.each(
  ['off-on', 'a-b', 'same-grant'].flatMap((transition) =>
    [
      'then',
      'await',
      'return-known',
      'same-error',
      'fresh-callback',
      'fresh-await',
      'unknown-native',
      'unknown-then',
      'unknown-await',
      'fresh-unknown-callback',
      'fresh-unknown-await',
      'catch-new',
      'primitive',
    ].map((mode) => ({ transition, mode })),
  ),
)(
  'keeps rejected-work provenance across semantic operations: $transition/$mode',
  ({ transition, mode }) => {
    const output = execFileSync(
      process.execPath,
      [join(directory, 'derived.mjs'), `${transition}:${mode}`],
      { encoding: 'utf8', timeout: 15_000 },
    );
    const captured =
      mode.startsWith('fresh') ||
      mode === 'catch-new' ||
      (transition === 'same-grant' && !mode.startsWith('unknown') && mode !== 'primitive');
    expect(JSON.parse(output)).toEqual({
      pending: captured ? 1 : 0,
      freshPending: captured ? 2 : 1,
      unhandled: 2,
      warnings: 0,
      strictFatals: 0,
      firstMatches: true,
      freshMatched: true,
    });
    expect(output).not.toContain('PRIVATE_');
  },
);

it('preserves handled rejections and fulfillment without retaining or mutating errors', () => {
  const output = execFileSync(
    process.execPath,
    ['--unhandled-rejections=strict', '--expose-gc', join(directory, 'handled.mjs')],
    { encoding: 'utf8', timeout: 15_000 },
  );
  expect(JSON.parse(output)).toEqual({ handled: true, retained: false });
});

it.each([
  'own-constructor',
  'own-then',
  'prototype-constructor',
  'species-getter',
  'custom-species',
  'foreign',
])('keeps unsupported native promise consumers local without extra user hooks: %s', (mode) => {
  const output = execFileSync(process.execPath, [join(directory, 'guards.mjs'), mode], {
    encoding: 'utf8',
    timeout: 15_000,
  });
  expect(JSON.parse(output)).toEqual({
    calls: mode === 'foreign' ? 0 : 1,
    unhandled: 1,
    warnings: 0,
    local: true,
  });
});

it.each(['unknown-then', 'unknown-await', 'fresh-unknown-await'])(
  'preserves strict-mode fatal and unhandled delivery for %s',
  (mode) => {
    const output = execFileSync(
      process.execPath,
      ['--unhandled-rejections=strict', join(directory, 'derived.mjs'), `off-on:${mode}`],
      { encoding: 'utf8', timeout: 15_000 },
    );
    const captured = mode.startsWith('fresh');
    expect(JSON.parse(output)).toEqual({
      pending: captured ? 1 : 0,
      freshPending: captured ? 2 : 1,
      unhandled: 2,
      warnings: 0,
      strictFatals: 2,
      firstMatches: true,
      freshMatched: true,
    });
  },
);


beforeAll(async () => {
  await writeFile(join(directory, 'fulfillment.mjs'), String.raw`
    import assert from 'node:assert/strict';
    import { DiagnosticReports, installDiagnosticProcessCapture, runDiagnosticOperation } from './capture.mjs';
    import { mkdtemp, rm } from 'node:fs/promises'; import { tmpdir } from 'node:os'; import { join } from 'node:path';
    const directory = await mkdtemp(join(tmpdir(), 'voice-fulfillment-observer-'));
    const reports = new DiagnosticReports({directory,identity:{app_version:'1.0.0',build_commit:'0000000',platform:'linux',arch:'x64'},automatic:false,env:{},destination:{site:'fixture',host:'https://fixture.invalid',appUrl:'https://fixture.invalid'}});
    await reports.initialize();await reports.setConsent(true);const stop=installDiagnosticProcessCapture(reports);
    let reads=0,unhandled=0,warnings=0;const value={};
    process.on('unhandledRejection',()=>unhandled++);process.on('warning',()=>warnings++);
    const poison=()=>Object.defineProperty(value,'then',{get(){reads++;throw new Error('fixture unexpected assimilation');}});
    let original,result;
    if(process.argv[2]==='returned') {
      result=runDiagnosticOperation(reports,()=>{original=Promise.resolve(value);poison();return original;});
    } else {
      original=Promise.resolve(value);await new Promise(setImmediate);poison();
      result=runDiagnosticOperation(reports,()=>original.then(item=>{assert.equal(item,value);return 42;}));
    }
    let output,failed=false;
    try {output=await result.then(item=>{if(process.argv[2]==='returned')assert.equal(item,value);return 42;});}catch {failed=true;}
    await new Promise(setImmediate);await new Promise(setImmediate);
    console.log(JSON.stringify({output:output??null,failed,reads,unhandled,warnings,pending:reports.status().pending_count,samePromise:process.argv[2]!=='returned'||result===original}));
    stop();await reports.close();await rm(directory,{recursive:true,force:true});
  `);
});
it.each(['returned','consumed'])(
  'does not assimilate fulfilled objects in passive promise observation: %s', (mode) => {
    const output=execFileSync(process.execPath,[join(directory,'fulfillment.mjs'),mode],{encoding:'utf8',timeout:15000});
    expect(JSON.parse(output)).toEqual({output:42,failed:false,reads:0,unhandled:0,warnings:0,pending:0,samePromise:true});
  },
);


beforeAll(async () => {
  await writeFile(join(directory, 'unconsumed.mjs'), String.raw`
    import { DiagnosticReports, installDiagnosticProcessCapture, runDiagnosticOperation } from './capture.mjs';
    import { join } from 'node:path';
    const reports = new DiagnosticReports({
      directory: join(import.meta.dirname, 'unconsumed-' + process.argv[2]),
      identity: { app_version: '1.0.0', build_commit: '0000000', platform: 'linux', arch: 'x64' },
      automatic: false, env: {},
      destination: { site: 'fixture', host: 'https://fixture.invalid', appUrl: 'https://fixture.invalid' },
    });
    await reports.initialize(); await reports.setConsent(true);
    const stop = installDiagnosticProcessCapture(reports);
    let unhandled = 0, warnings = 0, original, seen;
    process.on('unhandledRejection', (_error, promise) => { unhandled++; seen = promise; });
    process.on('warning', () => warnings++);
    process.on('uncaughtExceptionMonitor', (_error, origin) => {
      console.log(JSON.stringify({ fatal: origin }));
    });
    original = runDiagnosticOperation(reports, async () => {
      await 0; await null; await undefined;
      throw new Error('SYNTHETIC_UNCONSUMED_FAILURE');
    });
    // Deliberately never attach a Promise consumer. A timer keeps the process
    // alive long enough to observe missing events without a hanging RED test.
    await new Promise(resolve => setTimeout(resolve, 50));
    console.log(JSON.stringify({ unhandled, warnings, pending: reports.status().pending_count, samePromise: seen === original }));
    stop(); await reports.close();
  `);
});

it('preserves an unconsumed rejection after a non-Promise await', () => {
  const child = spawnSync(process.execPath, [join(directory, 'unconsumed.mjs'), 'normal'], {
    encoding: 'utf8', timeout: 15_000,
  });
  expect(child.error).toBeUndefined();
  expect(child.status, child.stderr).toBe(0);
  expect(JSON.parse(child.stdout.trim())).toEqual({ unhandled: 1, warnings: 0, pending: 1, samePromise: true });
});

it('preserves strict-mode fatal policy for an unconsumed non-Promise await', () => {
  const child = spawnSync(process.execPath, ['--unhandled-rejections=strict', join(directory, 'unconsumed.mjs'), 'strict'], {
    encoding: 'utf8', timeout: 15_000,
  });
  expect(child.error).toBeUndefined();
  expect(child.status).toBe(1);
  expect(child.stdout.trim()).toBe(JSON.stringify({ fatal: 'unhandledRejection' }));
  expect(child.stderr).toContain('SYNTHETIC_UNCONSUMED_FAILURE');
});


beforeAll(async () => {
  await writeFile(join(directory, 'caught-inner.mjs'), String.raw`
    import { DiagnosticReports, installDiagnosticProcessCapture, runDiagnosticOperation, diagnosticOperationOwner } from './capture.mjs';
    import { AsyncResource } from 'node:async_hooks';
    import { join } from 'node:path';
    const [transition, mode] = process.argv[2].split(':');
    const reports = new DiagnosticReports({
      directory: join(import.meta.dirname, 'caught-' + transition + '-' + mode + '-' + (process.execArgv.includes('--unhandled-rejections=strict') ? 'strict' : 'normal')),
      identity: { app_version: '1.0.0', build_commit: '0000000', platform: 'linux', arch: 'x64' },
      automatic: false, env: {}, destination: { site: 'fixture', host: 'https://fixture.invalid', appUrl: 'https://fixture.invalid' },
    });
    await reports.initialize(); if (transition !== 'off-on') await reports.setConsent(true);
    const stop = installDiagnosticProcessCapture(reports);
    const outside = new AsyncResource('fixture-outside-owner');
    let release, saved, unhandled = 0, fatal = 0, earlyLocal = true;
    const held = new Promise(resolve => { release = resolve; });
    process.on('unhandledRejection', () => unhandled++);
    // Test-only listener observes strict escalation while keeping the child alive
    // for the pending-count assertion. The separate strict test proves exit.
    process.on('uncaughtException', (_error, origin) => { if (origin !== 'unhandledRejection') process.exit(91); fatal++; });
    const delivered = new Promise(resolve => process.once('unhandledRejection', () => setImmediate(resolve)));
    const original = runDiagnosticOperation(reports, async () => {
      try {
        await runDiagnosticOperation(reports, async () => {
          await held; await 0; await null;
          if (mode !== 'successful-data') throw new Error('SYNTHETIC_OLD_FAILURE');
          return 42;
        });
      } catch (error) {
        if (mode === 'synchronous-escape') outside.runInAsyncScope(() => {
          try { runDiagnosticOperation(reports, () => { throw error; }); }
          catch (escaped) {
            // Check the synchronous handoff before any observer microtask can
            // run; it must not grant permission merely because B is current.
            earlyLocal = diagnosticOperationOwner(reports, escaped)?.epoch === null;
            throw escaped;
          }
        });
        saved = error;
      }
    });
    if (transition === 'off-on') await reports.setConsent(true);
    if (transition === 'a-b') { await reports.setConsent(false); await reports.setConsent(true); }
    release();
    if (mode !== 'synchronous-escape') {
      await original;
      runDiagnosticOperation(reports, async () => { throw mode === 'successful-data' ? new Error('SYNTHETIC_NEW_FAILURE') : saved; });
    }
    await delivered; await new Promise(setImmediate);
    console.log(JSON.stringify({ pending: reports.status().pending_count, unhandled, fatal, earlyLocal }));
    outside.emitDestroy(); stop(); await reports.close();
  `);
});

it.each(['off-on', 'a-b', 'same-grant'].flatMap(transition =>
  ['caught-later', 'synchronous-escape', 'successful-data'].flatMap(mode =>
    [false, true].map(strict => ({ transition, mode, strict }))),
))('retains caught inner ownership without suppressing fresh data failures: $transition/$mode/strict=$strict', ({ transition, mode, strict }) => {
  const output = execFileSync(process.execPath, [
    ...(strict ? ['--unhandled-rejections=strict'] : []),
    join(directory, 'caught-inner.mjs'), `${transition}:${mode}`,
  ], { encoding: 'utf8', timeout: 15_000 });
  expect(JSON.parse(output)).toEqual({
    pending: transition === 'same-grant' || mode === 'successful-data' ? 1 : 0,
    unhandled: 1,
    fatal: strict ? 1 : 0,
    earlyLocal: true,
  });
});
