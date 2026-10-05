// Isolated proof that the direct adapter works with serialization unavailable.
export async function resolve(specifier, context, nextResolve) {
  if (specifier === '@icp-sdk/core/candid') {
    const actual = await nextResolve(specifier, context);
    const code = `
      import * as sdk from ${JSON.stringify(actual.url)};
      export const IDL = new Proxy(sdk.IDL, { get(target, key) {
        if (key === 'encode') return () => { throw new Error('ordinary IDL.encode is unavailable'); };
        return Reflect.get(target, key);
      }});
      export const idlLabelToId = sdk.idlLabelToId;
      export const lebDecode = sdk.lebDecode;
      export const safeRead = sdk.safeRead;
    `;
    return { url: `data:text/javascript,${encodeURIComponent(code)}`, shortCircuit: true };
  }
  return nextResolve(specifier, context);
}

export async function load(url, context, nextLoad) {
  if (url.endsWith('/dist/index.js')) return {
    format: 'module', shortCircuit: true,
    source: `export const canonicalize = () => { throw new Error('canonicalize is unavailable'); };`,
  };
  return nextLoad(url, context);
}
