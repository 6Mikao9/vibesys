import {expect, test} from 'bun:test';
import {GatewayError, type GatewayUnaryReply, gatewayBridge, parseGatewayRequest} from './index.js';

function preloadAnswering(reply: GatewayUnaryReply) {
  return gatewayBridge({
    unary: () => ({result: Promise.resolve(reply), cancel: () => undefined}),
    stream: () => ({cancel: () => undefined}),
  });
}

test('generic gateway calls validate all routing keys and preserve protobuf bytes', () => {
  const request = {
    kind: 'stream' as const,
    host: 'ssh:host-a',
    method: '/vibesys.api.v1.HostService/Tail',
    bytes: new Uint8Array([1, 2]),
  };
  expect(parseGatewayRequest(request)).toEqual(request);
  for (const change of [
    {kind: 'bidi'},
    {host: '-oProxyCommand=x'},
    {method: 'Tail'},
    {bytes: []},
    {extra: true},
  ]) {
    expect(() => parseGatewayRequest({...request, ...change})).toThrow();
  }
});

test('a unary failure keeps its gRPC status across the preload, for every status', async () => {
  for (let status = 0; status <= 16; status++) {
    const error = await preloadAnswering({error: 'no such run', status})
      .unary('ssh:host-a', '/vibesys.api.v1.HostService/Skeleton', new Uint8Array())
      .result.catch((error: unknown) => error);
    expect(error).toBeInstanceOf(GatewayError);
    expect((error as GatewayError).status).toBe(status);
    expect((error as Error).message).toBe('no such run');
  }
  const plain = await preloadAnswering({error: 'Run transport v2 is off'})
    .unary('ssh:host-a', '/vibesys.api.v1.HostService/Skeleton', new Uint8Array())
    .result.catch((error: unknown) => error);
  expect((plain as GatewayError).status).toBeUndefined();
  expect((plain as Error).message).toBe('Run transport v2 is off');
  expect(
    await preloadAnswering({bytes: new Uint8Array([7])}).unary(
      'ssh:host-a',
      '/vibesys.api.v1.HostService/Skeleton',
      new Uint8Array(),
    ).result,
  ).toEqual(new Uint8Array([7]));
});
