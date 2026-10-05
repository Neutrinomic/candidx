use serde_json::Value;

#[test]
fn shared_corpus_and_identity_rules() {
    let data: Value = serde_json::from_str(include_str!("vectors.json")).unwrap();
    for entry in data["vectors"].as_array().unwrap() {
        let expected = hex::decode(entry["canonical"].as_str().unwrap()).unwrap();
        for input in entry["raw"].as_object().unwrap().values() {
            let bytes = hex::decode(input.as_str().unwrap()).unwrap();
            assert_eq!(
                candidx::canonicalize(&bytes).unwrap(),
                expected,
                "{}",
                entry["id"]
            );
        }
        assert_eq!(candidx::canonicalize(&expected).unwrap(), expected);
    }
    for entry in data["positive_edges"].as_array().unwrap() {
        let bytes = hex::decode(entry["raw"].as_str().unwrap()).unwrap();
        let expected = hex::decode(entry["canonical"].as_str().unwrap()).unwrap();
        assert_eq!(
            candidx::canonicalize(&bytes).unwrap(),
            expected,
            "{}",
            entry["id"]
        );
    }
    for entry in data["negative_cases"].as_array().unwrap() {
        let bytes = hex::decode(entry["raw"].as_str().unwrap()).unwrap();
        assert!(candidx::canonicalize(&bytes).is_err(), "{}", entry["id"]);
    }
}

#[cfg(feature = "candid")]
#[test]
fn typed_record_extension_and_projection() {
    use candid::{CandidType, Deserialize};
    #[derive(CandidType, Deserialize, PartialEq, Debug)]
    struct Old {
        a: String,
    }
    #[derive(CandidType, Deserialize, PartialEq, Debug)]
    struct New {
        a: String,
        b: Option<String>,
    }
    let old = candidx::encode_one(Old { a: "abc".into() }).unwrap();
    let new = candidx::encode_one(New {
        a: "abc".into(),
        b: None,
    })
    .unwrap();
    assert_eq!(old, new);
    assert_eq!(
        candid::decode_one::<New>(&old).unwrap(),
        New {
            a: "abc".into(),
            b: None
        }
    );
    let present = candidx::encode_one(New {
        a: "abc".into(),
        b: Some("new data".into()),
    })
    .unwrap();
    assert_ne!(present, old);
    // This receiver loses b. Its re-encoded bytes cannot verify the original.
    let projected = candid::decode_one::<Old>(&present).unwrap();
    assert_eq!(candidx::encode_one(projected).unwrap(), old);
}

#[cfg(feature = "candid")]
#[test]
fn typed_argument_sequence_preserves_order_and_framing() {
    let bytes = candidx::encode_args((candid::Nat::from(128u32), "abc".to_string())).unwrap();
    let decoded: (candid::Nat, String) = candid::decode_args(&bytes).unwrap();
    assert_eq!(decoded, (candid::Nat::from(128u32), "abc".to_string()));
    assert_ne!(
        bytes,
        candidx::encode_one((candid::Nat::from(128u32), "abc".to_string())).unwrap()
    );
}
