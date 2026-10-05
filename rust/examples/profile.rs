//! Host-side allocator measurements; this example never hashes values.
mod support;
use serde_json::{json, Value};
use std::alloc::{GlobalAlloc, Layout, System};
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering::Relaxed};

struct Tracked;
static ENABLED: AtomicBool = AtomicBool::new(false);
static ALLOCATED: AtomicUsize = AtomicUsize::new(0);
static ALLOCATIONS: AtomicUsize = AtomicUsize::new(0);
static LIVE: AtomicUsize = AtomicUsize::new(0);
static PEAK: AtomicUsize = AtomicUsize::new(0);

fn add(bytes: usize) {
    ALLOCATED.fetch_add(bytes, Relaxed);
    ALLOCATIONS.fetch_add(1, Relaxed);
    let live = LIVE.fetch_add(bytes, Relaxed) + bytes;
    PEAK.fetch_max(live, Relaxed);
}
unsafe impl GlobalAlloc for Tracked {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        let pointer = unsafe { System.alloc(layout) };
        if ENABLED.load(Relaxed) && !pointer.is_null() {
            add(layout.size());
        }
        pointer
    }
    unsafe fn dealloc(&self, pointer: *mut u8, layout: Layout) {
        if ENABLED.load(Relaxed) {
            LIVE.fetch_sub(layout.size(), Relaxed);
        }
        unsafe { System.dealloc(pointer, layout) };
    }
    unsafe fn realloc(&self, pointer: *mut u8, layout: Layout, bytes: usize) -> *mut u8 {
        let new = unsafe { System.realloc(pointer, layout, bytes) };
        if ENABLED.load(Relaxed) && !new.is_null() {
            LIVE.fetch_sub(layout.size(), Relaxed);
            add(bytes);
        }
        new
    }
}
#[global_allocator]
static ALLOCATOR: Tracked = Tracked;

fn main() {
    let path = std::env::args().nth(1).expect("profile BENCH.json");
    let fixture: Value = serde_json::from_slice(&std::fs::read(path).unwrap()).unwrap();
    let mut cases = Vec::new();
    for case in fixture["cases"].as_array().unwrap() {
        let raw = hex::decode(case["raw_hex"].as_str().unwrap()).unwrap();
        let expected = hex::decode(case["canonical_hex"].as_str().unwrap()).unwrap();
        let name = case["name"].as_str().unwrap();
        let (_, count, memo_size) = support::WORKLOADS
            .iter()
            .find(|(candidate, _, _)| *candidate == name)
            .unwrap();
        let block = support::block(*count, *memo_size);
        // Initialize shared type caches and all application fixtures outside
        // the counters. Each measured typed encoder still derives its schema
        // exactly as its real public entry point does.
        assert_eq!(candidx::encode_one(&block).unwrap(), expected);
        assert_eq!(candid::encode_one(&block).unwrap(), raw);
        let mut stats = json!({"name":name});
        for phase in [
            "ordinary_encode",
            "canonical_encode_direct",
            "canonical_encode_pipeline",
            "canonicalize_preencoded",
        ] {
            // Remove prior per-operation schema values before counting; their
            // allocations belong to the preceding operation, not this one.
            candid::types::internal::env_clear();
            ALLOCATED.store(0, Relaxed);
            ALLOCATIONS.store(0, Relaxed);
            LIVE.store(0, Relaxed);
            PEAK.store(0, Relaxed);
            ENABLED.store(true, Relaxed);
            let output = match phase {
                "ordinary_encode" => candid::encode_one(&block).unwrap(),
                "canonical_encode_direct" => candidx::encode_one(&block).unwrap(),
                "canonical_encode_pipeline" => {
                    let ordinary = candid::encode_one(&block).unwrap();
                    candidx::canonicalize(&ordinary).unwrap()
                }
                "canonicalize_preencoded" => candidx::canonicalize(&raw).unwrap(),
                _ => unreachable!(),
            };
            ENABLED.store(false, Relaxed);
            let live = LIVE.load(Relaxed);
            let peak = PEAK.load(Relaxed);
            assert!(
                live <= peak && peak <= ALLOCATED.load(Relaxed),
                "allocator accounting underflow"
            );
            stats[phase] = json!({
                "allocated_bytes":ALLOCATED.load(Relaxed),
                "allocation_requests":ALLOCATIONS.load(Relaxed),
                "peak_live_allocation_bytes":peak,
                "operation_end_measured_allocation_bytes":live,
                "retained_output_capacity_bytes":output.capacity(),
            });
            assert_eq!(
                output.as_slice(),
                if phase == "ordinary_encode" {
                    raw.as_slice()
                } else {
                    expected.as_slice()
                }
            );
        }
        cases.push(stats);
    }
    println!(
        "{}",
        json!({"language":"Rust", "method":"System allocator requested bytes, counting whole realloc requests; fixtures and type-cache warmup outside counters; typed encoders' own schema allocations included; excludes allocator metadata and pre-existing input", "cases":cases})
    );
}
