//! Shared seeded-corpus typed encoder for tests/fuzz.py. Not a shipped API.
use candid::types::value::{IDLArgs, IDLField, IDLValue, VariantValue};
use candid::types::{Field, Label, Type, TypeEnv, TypeInner};
use serde_json::{json, Value};
use std::rc::Rc;

fn field(label: &str, ty: Type) -> Field {
    Field {
        id: Rc::new(Label::Named(label.into())),
        ty,
    }
}

fn ty(schema: &Value, env: &mut TypeEnv, counter: &mut usize) -> Type {
    let s = schema.as_array().unwrap();
    let kind = s[0].as_str().unwrap();
    match kind {
        "opt" => TypeInner::Opt(ty(&s[1], env, counter)),
        "vec" => TypeInner::Vec(ty(&s[1], env, counter)),
        "blob" => TypeInner::Vec(TypeInner::Nat8.into()),
        "record" | "variant" => {
            let mut fields: Vec<_> = s[1..]
                .iter()
                .map(|f| field(f[0].as_str().unwrap(), ty(&f[1], env, counter)))
                .collect();
            fields.sort_by_key(|f| f.id.get_id());
            if kind == "record" {
                TypeInner::Record(fields)
            } else {
                TypeInner::Variant(fields)
            }
        }
        "tuple" => TypeInner::Record(
            s[1..]
                .iter()
                .enumerate()
                .map(|(index, schema)| Field {
                    id: Rc::new(Label::Id(index as u32)),
                    ty: ty(schema, env, counter),
                })
                .collect(),
        ),
        "chain" => {
            let name = format!("FuzzChain{}", *counter);
            *counter += 1;
            let reference: Type = TypeInner::Var(name.clone()).into();
            let mut fields = vec![
                field("head", TypeInner::Nat.into()),
                field("tail", TypeInner::Opt(reference.clone()).into()),
            ];
            fields.sort_by_key(|field| field.id.get_id());
            env.0.insert(name, TypeInner::Record(fields).into());
            return reference;
        }
        "list" => {
            let name = format!("FuzzList{}", *counter);
            *counter += 1;
            let reference: Type = TypeInner::Var(name.clone()).into();
            let mut fields = vec![
                field("item", ty(&s[1], env, counter)),
                field("next", reference.clone()),
            ];
            if s[2].as_bool().unwrap_or(false) {
                fields.push(field(
                    "extra",
                    TypeInner::Opt(TypeInner::Text.into()).into(),
                ));
            }
            fields.sort_by_key(|f| f.id.get_id());
            env.0.insert(
                name,
                TypeInner::Opt(TypeInner::Record(fields).into()).into(),
            );
            return reference;
        }
        "null" => TypeInner::Null,
        "reserved" => TypeInner::Reserved,
        "bool" => TypeInner::Bool,
        "nat" => TypeInner::Nat,
        "int" => TypeInner::Int,
        "nat8" => TypeInner::Nat8,
        "nat16" => TypeInner::Nat16,
        "nat32" => TypeInner::Nat32,
        "nat64" => TypeInner::Nat64,
        "int8" => TypeInner::Int8,
        "int16" => TypeInner::Int16,
        "int32" => TypeInner::Int32,
        "int64" => TypeInner::Int64,
        "float32" | "float32bits" => TypeInner::Float32,
        "float64" | "float64bits" => TypeInner::Float64,
        "text" => TypeInner::Text,
        "principal" => TypeInner::Principal,
        _ => panic!("bad audit schema kind: {kind}"),
    }
    .into()
}

fn val(schema: &Value, v: &Value) -> IDLValue {
    let s = schema.as_array().unwrap();
    let kind = s[0].as_str().unwrap();
    match kind {
        "opt" => {
            if v.as_array().unwrap().is_empty() {
                IDLValue::None
            } else {
                IDLValue::Opt(Box::new(val(&s[1], &v[0])))
            }
        }
        "vec" => IDLValue::Vec(
            v.as_array()
                .unwrap()
                .iter()
                .map(|x| val(&s[1], x))
                .collect(),
        ),
        "blob" => IDLValue::Blob(
            v.as_array()
                .unwrap()
                .iter()
                .map(|x| x.as_u64().unwrap() as u8)
                .collect(),
        ),
        "record" => IDLValue::Record(
            s[1..]
                .iter()
                .map(|f| {
                    let name = f[0].as_str().unwrap();
                    IDLField {
                        id: Label::Named(name.into()),
                        val: val(&f[1], &v[name]),
                    }
                })
                .collect(),
        ),
        "tuple" => IDLValue::Record(
            s[1..]
                .iter()
                .enumerate()
                .map(|(index, schema)| IDLField {
                    id: Label::Id(index as u32),
                    val: val(schema, &v[index]),
                })
                .collect(),
        ),
        "chain" => IDLValue::Record(vec![
            IDLField {
                id: Label::Named("head".into()),
                val: IDLValue::Nat(v["head"].as_str().unwrap().parse().unwrap()),
            },
            IDLField {
                id: Label::Named("tail".into()),
                val: if v["tail"].as_array().unwrap().is_empty() {
                    IDLValue::None
                } else {
                    IDLValue::Opt(Box::new(val(schema, &v["tail"][0])))
                },
            },
        ]),
        "variant" => {
            let (name, value) = v.as_object().unwrap().iter().next().unwrap();
            let f = s[1..].iter().find(|f| f[0].as_str() == Some(name)).unwrap();
            IDLValue::Variant(VariantValue(
                Box::new(IDLField {
                    id: Label::Named(name.into()),
                    val: val(&f[1], value),
                }),
                0,
            ))
        }
        "list" => {
            if v.as_array().unwrap().is_empty() {
                return IDLValue::None;
            }
            let mut fields = vec![
                IDLField {
                    id: Label::Named("item".into()),
                    val: val(&s[1], &v[0]["item"]),
                },
                IDLField {
                    id: Label::Named("next".into()),
                    val: val(schema, &v[0]["next"]),
                },
            ];
            if s[2].as_bool().unwrap_or(false) {
                fields.push(IDLField {
                    id: Label::Named("extra".into()),
                    val: val(&json!(["opt", ["text"]]), &v[0]["extra"]),
                });
            }
            IDLValue::Opt(Box::new(IDLValue::Record(fields)))
        }
        "null" => IDLValue::Null,
        "reserved" => IDLValue::Reserved,
        "bool" => IDLValue::Bool(v.as_bool().unwrap()),
        "nat" => IDLValue::Nat(v.as_str().unwrap().parse().unwrap()),
        "int" => IDLValue::Int(v.as_str().unwrap().parse().unwrap()),
        "nat8" => IDLValue::Nat8(v.as_u64().unwrap() as u8),
        "nat16" => IDLValue::Nat16(v.as_u64().unwrap() as u16),
        "nat32" => IDLValue::Nat32(v.as_u64().unwrap() as u32),
        "nat64" => IDLValue::Nat64(v.as_str().unwrap().parse().unwrap()),
        "int8" => IDLValue::Int8(v.as_i64().unwrap() as i8),
        "int16" => IDLValue::Int16(v.as_i64().unwrap() as i16),
        "int32" => IDLValue::Int32(v.as_i64().unwrap() as i32),
        "int64" => IDLValue::Int64(v.as_str().unwrap().parse().unwrap()),
        "float32" => IDLValue::Float32(float(v) as f32),
        "float64" => IDLValue::Float64(float(v)),
        "float32bits" => IDLValue::Float32(f32::from_bits(bits(v) as u32)),
        "float64bits" => IDLValue::Float64(f64::from_bits(bits(v))),
        "text" => IDLValue::Text(v.as_str().unwrap().into()),
        "principal" => IDLValue::Principal(candid::Principal::from_slice(
            &v.as_array()
                .unwrap()
                .iter()
                .map(|x| x.as_u64().unwrap() as u8)
                .collect::<Vec<_>>(),
        )),
        _ => panic!("bad audit value kind: {kind}"),
    }
}

fn float(v: &Value) -> f64 {
    if let Some(number) = v.as_f64() {
        return number;
    }
    match v.as_str().unwrap() {
        "nan" => f64::NAN,
        "inf" => f64::INFINITY,
        "-inf" => f64::NEG_INFINITY,
        text => text.parse().unwrap(),
    }
}
fn bits(v: &Value) -> u64 {
    if let Some(number) = v.as_u64() {
        return number;
    }
    let text = v.as_str().unwrap();
    match text.strip_prefix("0x") {
        Some(text) => u64::from_str_radix(text, 16).unwrap(),
        None => text.parse().unwrap(),
    }
}
fn same_value(a: &IDLValue, b: &IDLValue) -> bool {
    match (a, b) {
        (IDLValue::Float32(a), IDLValue::Float32(b)) => {
            (a.is_nan() && b.is_nan()) || a.to_bits() == b.to_bits()
        }
        (IDLValue::Float64(a), IDLValue::Float64(b)) => {
            (a.is_nan() && b.is_nan()) || a.to_bits() == b.to_bits()
        }
        (IDLValue::Opt(a), IDLValue::Opt(b)) => same_value(a, b),
        (IDLValue::Vec(a), IDLValue::Vec(b)) => {
            a.len() == b.len() && a.iter().zip(b).all(|(a, b)| same_value(a, b))
        }
        (IDLValue::Record(a), IDLValue::Record(b)) => {
            a.len() == b.len()
                && a.iter()
                    .zip(b)
                    .all(|(a, b)| a.id.get_id() == b.id.get_id() && same_value(&a.val, &b.val))
        }
        (IDLValue::Variant(a), IDLValue::Variant(b)) => {
            a.0.id.get_id() == b.0.id.get_id() && same_value(&a.0.val, &b.0.val)
        }
        _ => a == b,
    }
}

fn recover_original(
    bytes: &[u8],
    env: &TypeEnv,
    types: &[Type],
    original: &[IDLValue],
) -> Result<IDLArgs, String> {
    // Candid's typed decoder projects away extra arguments. The test's x is
    // the complete argument sequence, so check its original arity separately.
    if IDLArgs::from_bytes(bytes)
        .map_err(|error| format!("untyped decode failed: {error}"))?
        .args
        .len()
        != original.len()
    {
        return Err("argument/root count differs from original typed input".into());
    }
    let decoded = IDLArgs::from_bytes_with_types(bytes, env, types)
        .map_err(|error| format!("typed decode failed: {error}"))?;
    if decoded.args.len() != original.len()
        || !decoded
            .args
            .iter()
            .zip(original)
            .all(|(decoded, original)| same_value(decoded, original))
    {
        return Err(format!(
            "decoded value differs from original typed input: original={original:?}, decoded={:?}",
            decoded.args
        ));
    }
    Ok(decoded)
}

fn main() {
    let path = std::env::args().nth(1).expect("shared JSON corpus path");
    let corpus: Value = serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
    let decode_only = corpus["decode_only"].as_bool().unwrap_or(false);
    let comparison_only = corpus["comparison_only"].as_bool().unwrap_or(false);
    let mut failed = false;
    for c in corpus["cases"].as_array().unwrap() {
        let mut env = TypeEnv::new();
        let schemas: Vec<_> = c
            .get("schemas")
            .and_then(Value::as_array)
            .map(|s| s.iter().collect())
            .unwrap_or_else(|| vec![&c["schema"]]);
        let values: Vec<_> = c
            .get("values")
            .and_then(Value::as_array)
            .map(|s| s.iter().collect())
            .unwrap_or_else(|| vec![&c["value"]]);
        let mut counter = 0;
        let value_count = values.len();
        let types: Vec<_> = schemas
            .iter()
            .map(|s| ty(s, &mut env, &mut counter))
            .collect();
        let args = IDLArgs::new(
            &schemas
                .iter()
                .zip(values)
                .map(|(s, v)| val(s, v))
                .collect::<Vec<_>>(),
        );
        let result = (|| -> Result<Value, String> {
            if schemas.len() != value_count {
                return Err("original source argument count mismatch".into());
            }
            // Typed annotation sorts fields and resolves source variant indices;
            // it does not encode or parse any ordinary wire bytes.
            let typed_values = args
                .args
                .iter()
                .zip(&types)
                .map(|(v, t)| v.annotate_type(true, &env, t).map_err(|e| e.to_string()))
                .collect::<Result<Vec<_>, _>>()?;
            if comparison_only {
                let decoded_schemas = c["decoded_schemas"]
                    .as_array()
                    .ok_or("comparison case needs decoded_schemas")?;
                let decoded_values = c["decoded_values"]
                    .as_array()
                    .ok_or("comparison case needs decoded_values")?;
                if decoded_schemas.len() != decoded_values.len() {
                    return Err("mutant source argument count mismatch".into());
                }
                let mut decoded_env = TypeEnv::new();
                let mut decoded_counter = 0;
                let decoded_types: Vec<_> = decoded_schemas
                    .iter()
                    .map(|schema| ty(schema, &mut decoded_env, &mut decoded_counter))
                    .collect();
                let decoded = decoded_schemas
                    .iter()
                    .zip(decoded_values)
                    .zip(&decoded_types)
                    .map(|((schema, value), ty)| {
                        val(schema, value)
                            .annotate_type(true, &decoded_env, ty)
                            .map_err(|error| error.to_string())
                    })
                    .collect::<Result<Vec<_>, _>>()?;
                let equal = decoded.len() == typed_values.len()
                    && decoded
                        .iter()
                        .zip(&typed_values)
                        .all(|(decoded, original)| same_value(decoded, original));
                return Ok(json!({"id":c["id"], "comparison_equal":equal}));
            }
            if decode_only {
                let inputs = c["decode_inputs"]
                    .as_object()
                    .ok_or("decoder-only case needs decode_inputs")?;
                let mut roundtrips = serde_json::Map::new();
                for (encoder, encoded) in inputs {
                    let bytes = hex::decode(encoded.as_str().ok_or("hex input must be text")?)
                        .map_err(|error| error.to_string())?;
                    recover_original(&bytes, &env, &types, &typed_values)
                        .map_err(|error| format!("{encoder}: {error}"))?;
                    roundtrips.insert(encoder.clone(), json!("exact"));
                }
                return Ok(json!({"id":c["id"],"roundtrips":roundtrips}));
            }
            let canonical = candidx::encode_args_with_types(&typed_values, &env, &types)
                .map_err(|e| e.to_string())?;
            let decoded = recover_original(&canonical, &env, &types, &typed_values)?;
            let raw = args
                .to_bytes_with_types(&env, &types)
                .map_err(|e| e.to_string())?;
            recover_original(&raw, &env, &types, &typed_values)?;
            if candidx::canonicalize(&raw).map_err(|e| e.to_string())? != canonical {
                return Err("direct/inbound byte disagreement".into());
            }
            if candidx::canonicalize(&canonical).map_err(|e| e.to_string())? != canonical {
                return Err("canonical idempotence failure".into());
            }
            let recoded = decoded
                .to_bytes_with_types(&env, &types)
                .map_err(|e| e.to_string())?;
            if candidx::canonicalize(&recoded).map_err(|e| e.to_string())? != canonical {
                return Err("decoded re-encoding changed canonical bytes".into());
            }
            Ok(
                json!({ "id": c["id"], "raw": hex::encode(&raw), "canonical": hex::encode(&canonical), "decoding":"exact", "canonical_roundtrip":true, "ordinary_roundtrip":true, "ordinary_reencoding_exact":recoded == raw, "direct_path":"typed-values" }),
            )
        })();
        match result {
            Ok(output) => println!("{output}"),
            Err(error) => {
                failed = true;
                println!("{}", json!({ "id": c["id"], "error": error }));
            }
        }
    }
    if failed {
        std::process::exit(1);
    }
}
