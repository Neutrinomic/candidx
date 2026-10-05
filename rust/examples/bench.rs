mod support;
use serde_json::json;
use std::{hint::black_box, time::Instant};
use support::{Block, WORKLOADS};

fn sample(mut f: impl FnMut() -> usize) -> serde_json::Value {
    for _ in 0..3 {
        black_box(f());
    }
    let start = Instant::now();
    black_box(f());
    let us = start.elapsed().as_secs_f64() * 1e6;
    let count = (25_000.0 / us.max(1.0)).clamp(2.0, 2000.0) as usize;
    let mut times = Vec::new();
    for _ in 0..7 {
        let start = Instant::now();
        for _ in 0..count {
            black_box(f());
        }
        times.push(start.elapsed().as_secs_f64() * 1e6 / count as f64);
    }
    times.sort_by(f64::total_cmp);
    json!({"median_us": times[3], "min_us": times[0], "max_us": times[6], "iterations_per_trial":count, "trials":7})
}
fn main() {
    let mut cases = Vec::new();
    for (name, count, memo_size) in WORKLOADS {
        let block = support::block(count, memo_size);
        let canonical = candidx::encode_one(&block).unwrap();
        let raw = candid::encode_one(&block).unwrap();
        assert_eq!(candidx::canonicalize(&raw).unwrap(), canonical);
        assert_eq!(candid::decode_one::<Block>(&raw).unwrap(), block);
        assert_eq!(candid::decode_one::<Block>(&canonical).unwrap(), block);
        cases.push(json!({"name":name, "transactions":count, "memo_bytes":memo_size,
            "ordinary_bytes":raw.len(), "canonical_bytes":canonical.len(),
            "raw_hex": hex::encode(&raw), "canonical_hex": hex::encode(&canonical),
            "ordinary_encode":sample(|| candid::encode_one(black_box(&block)).unwrap().len()),
            "canonicalize_preencoded":sample(|| candidx::canonicalize(black_box(&raw)).unwrap().len()),
            "canonical_encode_direct":sample(|| candidx::encode_one(black_box(&block)).unwrap().len()),
            "canonical_encode_pipeline":sample(|| {
                let ordinary = candid::encode_one(black_box(&block)).unwrap();
                candidx::canonicalize(&ordinary).unwrap().len()
            }),
            "ordinary_decode":sample(|| candid::decode_one::<Block>(black_box(&raw)).unwrap().tx.len()),
            "canonical_decode":sample(|| candid::decode_one::<Block>(black_box(&canonical)).unwrap().tx.len())}));
    }
    println!("{}", json!({"language":"Rust", "cases":cases}));
}
