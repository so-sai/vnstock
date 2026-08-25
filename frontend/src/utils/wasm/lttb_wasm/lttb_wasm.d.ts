/* tslint:disable */
/* eslint-disable */

/**
 * 4-Point: High+Low+LTTB per bucket, deduped and time-ordered, always keep first/last.
 * Returns indices (Uint32Array) mirroring `downsample4Point`.
 */
export function downsample4point_indices(buf: Float64Array, count: number, target: number, stride: number): Uint32Array;

/**
 * LTTB — return selected indices (Uint32Array) for Float64Array buf with stride.
 * Mirrors `frontend/src/utils/lttb.ts:lttbTyped` exactly (area via close, X via timestamp).
 */
export function lttb_indices(buf: Float64Array, count: number, target: number, stride: number): Uint32Array;

export type InitInput = RequestInfo | URL | Response | BufferSource | WebAssembly.Module;

export interface InitOutput {
    readonly memory: WebAssembly.Memory;
    readonly downsample4point_indices: (a: number, b: number, c: number, d: number, e: number) => [number, number];
    readonly lttb_indices: (a: number, b: number, c: number, d: number, e: number) => [number, number];
    readonly __wbindgen_externrefs: WebAssembly.Table;
    readonly __wbindgen_malloc: (a: number, b: number) => number;
    readonly __wbindgen_free: (a: number, b: number, c: number) => void;
    readonly __wbindgen_start: () => void;
}

export type SyncInitInput = BufferSource | WebAssembly.Module;

/**
 * Instantiates the given `module`, which can either be bytes or
 * a precompiled `WebAssembly.Module`.
 *
 * @param {{ module: SyncInitInput }} module - Passing `SyncInitInput` directly is deprecated.
 *
 * @returns {InitOutput}
 */
export function initSync(module: { module: SyncInitInput } | SyncInitInput): InitOutput;

/**
 * If `module_or_path` is {RequestInfo} or {URL}, makes a request and
 * for everything else, calls `WebAssembly.instantiate` directly.
 *
 * @param {{ module_or_path: InitInput | Promise<InitInput> }} module_or_path - Passing `InitInput` directly is deprecated.
 *
 * @returns {Promise<InitOutput>}
 */
export default function __wbg_init (module_or_path?: { module_or_path: InitInput | Promise<InitInput> } | InitInput | Promise<InitInput>): Promise<InitOutput>;
