import {expect, test} from 'bun:test';
import {GrpcError, grpcCode} from '@vibesys/transport';
import {type GatewayUnaryReply, gatewayBridge, parseGatewayRequest} from './index.js';

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
  for (let code = 0; code <= 16; code++) {
    const error = await preloadAnswering({error: 'no such run', code})
      .unary('ssh:host-a', '/vibesys.api.v1.HostService/Skeleton', new Uint8Array())
      .result.catch((error: unknown) => error);
    expect(error).toBeInstanceOf(GrpcError);
    expect(grpcCode(error)).toBe(code);
    expect((error as Error).message).toBe('no such run');
  }
  const plain = await preloadAnswering({error: 'Run transport v2 is off'})
    .unary('ssh:host-a', '/vibesys.api.v1.HostService/Skeleton', new Uint8Array())
    .result.catch((error: unknown) => error);
  expect(grpcCode(plain)).toBeUndefined();
  expect((plain as Error).message).toBe('Run transport v2 is off');
  expect(
    await preloadAnswering({bytes: new Uint8Array([7])}).unary(
      'ssh:host-a',
      '/vibesys.api.v1.HostService/Skeleton',
      new Uint8Array(),
    ).result,
  ).toEqual(new Uint8Array([7]));
});
