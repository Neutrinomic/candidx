use candidx::{encode_direct_args, DirectField, DirectValue as V};
use std::borrow::Cow;

#[test]
fn no_std_tree_preserves_options_binary_aliases_and_explicit_fields() {
    let bytes = [0, 255, 1];
    let blob = encode_direct_args(&[V::Blob(&bytes)]).unwrap();
    let array = encode_direct_args(&[V::Vec {
        elements: bytes.iter().map(|v| V::Nat8(*v)).collect(),
        binary: true,
    }])
    .unwrap();
    assert_eq!(blob, array);
    assert_eq!(
        encode_direct_args(&[V::Blob(&[])]).unwrap(),
        encode_direct_args(&[V::Vec {
            elements: vec![],
            binary: true
        }])
        .unwrap()
    );
    assert_ne!(
        encode_direct_args(&[V::Blob(&[])]).unwrap(),
        encode_direct_args(&[V::Vec {
            elements: vec![],
            binary: false
        }])
        .unwrap()
    );
    let fields = vec![
        DirectField {
            id: 0,
            optional: false,
            value: V::Null,
        },
        DirectField {
            id: 1,
            optional: false,
            value: V::Reserved,
        },
        DirectField {
            id: 2,
            optional: true,
            value: V::Option(None),
        },
        DirectField {
            id: 3,
            optional: true,
            value: V::Option(Some(Box::new(V::Option(None)))),
        },
    ];
    let canonical = encode_direct_args(&[V::Record(fields)]).unwrap();
    assert_eq!(candidx::canonicalize(&canonical).unwrap(), canonical);
    assert_eq!(
        hex::encode(canonical),
        "4449444c026c03007f017003016e7f010001"
    );
}

#[test]
fn no_std_tree_rejects_bad_metadata_and_enforces_wire_resource_limits() {
    let invalid = V::Record(vec![
        DirectField {
            id: 2,
            optional: false,
            value: V::Null,
        },
        DirectField {
            id: 1,
            optional: false,
            value: V::Null,
        },
    ]);
    assert!(encode_direct_args(&[invalid]).is_err());
    assert!(encode_direct_args(&[V::Record(vec![DirectField {
        id: 0,
        optional: true,
        value: V::Null
    }])])
    .is_err());
    assert!(encode_direct_args(&[V::Vec {
        elements: vec![V::Nat16(1)],
        binary: true
    }])
    .is_err());
    let binary = vec![0u8; 2_000_000];
    assert!(encode_direct_args(&[V::Blob(&binary)]).is_err());
    let accepted = encode_direct_args(&[V::Blob(&binary[..1_999_999])]).unwrap();
    assert_eq!(candidx::canonicalize(&accepted).unwrap(), accepted);
    let mut nested = V::Blob(&[1]);
    for _ in 0..256 {
        nested = V::Option(Some(Box::new(nested)));
    }
    assert!(encode_direct_args(&[nested]).is_err());
    let mut excessive = vec![0u8; 3585];
    excessive[3584] = 1;
    assert!(encode_direct_args(&[V::Nat(Cow::Borrowed(&excessive))]).is_err());
    let roots: Vec<_> = (0..1025)
        .map(|id| {
            V::Record(vec![DirectField {
                id,
                optional: false,
                value: V::Null,
            }])
        })
        .collect();
    assert!(encode_direct_args(&roots).is_err());
    let accepted = encode_direct_args(&roots[..1024]).unwrap();
    assert_eq!(candidx::canonicalize(&accepted).unwrap(), accepted);
}

#[cfg(feature = "candid")]
mod typed {
    use super::*;
    use candid::types::{Compound, Field, Label, Serializer, Type, TypeEnv, TypeInner};
    use candid::{CandidType, Deserialize, Int, Nat, Reserved};
    use std::cell::Cell;

    fn parity<T: CandidType>(value: &T) {
        let expected = candidx::canonicalize(&candid::encode_one(value).unwrap()).unwrap();
        let actual = candidx::encode_one(value).unwrap();
        assert_eq!(actual, expected);
        assert_eq!(candidx::canonicalize(&actual).unwrap(), actual);
    }
    #[derive(CandidType, Deserialize, PartialEq, Debug)]
    struct Unit;
    #[derive(CandidType, Deserialize, PartialEq, Debug)]
    struct Wrapped(u64);
    #[derive(CandidType, Deserialize, PartialEq, Debug)]
    struct Inner {
        a: u8,
        b: Option<String>,
    }
    #[derive(CandidType, Deserialize, PartialEq, Debug)]
    struct Empty {}
    #[derive(CandidType, Deserialize, PartialEq, Debug)]
    enum Choice {
        Unit,
        Pair(u8, bool),
        Fields { a: u8, b: Option<String> },
        Nested(Inner),
        Empty(Empty),
        Wrapped(Wrapped),
    }
    #[test]
    fn native_unit_newtype_and_flattened_variant_callbacks() {
        parity(&Unit);
        parity(&Wrapped(42));
        let choices = vec![
            Choice::Unit,
            Choice::Pair(255, true),
            Choice::Fields { a: 0, b: None },
            Choice::Fields {
                a: 1,
                b: Some("x".into()),
            },
            Choice::Nested(Inner { a: 2, b: None }),
            Choice::Empty(Empty {}),
            Choice::Wrapped(Wrapped(7)),
        ];
        for choice in &choices {
            parity(choice);
        }
        parity(&choices);
        assert_eq!(
            candid::decode_one::<Vec<Choice>>(&candidx::encode_one(&choices).unwrap()).unwrap(),
            choices
        );
    }
    #[derive(CandidType, Deserialize, PartialEq, Debug)]
    struct Recursive {
        item: Nat,
        next: Option<Box<Recursive>>,
        unused: Option<Reserved>,
    }
    #[test]
    fn recursive_records_and_present_option_nesting() {
        let value = Recursive {
            item: Nat::from(128u32),
            next: Some(Box::new(Recursive {
                item: Nat::from(0u32),
                next: None,
                unused: None,
            })),
            unused: None,
        };
        parity(&value);
        assert_eq!(
            candid::decode_one::<Recursive>(&candidx::encode_one(&value).unwrap()).unwrap(),
            value
        );
        parity(&vec![None, Some(None), Some(Some(Nat::from(7u32)))]);
        parity(&(
            Reserved,
            (),
            None::<Nat>,
            Some(None::<Nat>),
            Some(Some(Reserved)),
        ));
    }
    #[test]
    fn scalar_kinds_raw_primitive_vectors_and_integer_magnitudes() {
        for value in [
            i128::MIN,
            -129,
            -128,
            -65,
            -64,
            -1,
            0,
            1,
            63,
            64,
            127,
            128,
            i128::MAX,
        ] {
            parity(&value);
            let magnitude = value.unsigned_abs().to_le_bytes();
            assert_eq!(
                encode_direct_args(&[V::Int {
                    negative: value < 0,
                    magnitude: Cow::Borrowed(&magnitude)
                }])
                .unwrap(),
                candidx::encode_one(value).unwrap()
            );
        }
        for value in [0u128, 1, 127, 128, u128::MAX] {
            parity(&value);
            assert_eq!(
                encode_direct_args(&[V::Nat(Cow::Borrowed(&value.to_le_bytes()))]).unwrap(),
                candidx::encode_one(value).unwrap()
            );
        }
        let big: Int = "-1606938044258990275541962092341162602522202993782792835301376"
            .parse()
            .unwrap();
        parity(&big);
        let (_, magnitude) = big.0.to_bytes_le();
        assert_eq!(
            encode_direct_args(&[V::Int {
                negative: true,
                magnitude: Cow::Borrowed(&magnitude)
            }])
            .unwrap(),
            candidx::encode_one(&big).unwrap()
        );
        parity(&vec![false, true]);
        parity(&vec![0u8, 255]);
        parity(&vec![0u16, u16::MAX]);
        parity(&vec![i32::MIN, i32::MAX]);
        parity(&vec![u64::MAX, 0]);
        parity(&vec![f32::from_bits(0xff80_0001), -0.0, 0.0, f32::INFINITY]);
        parity(&vec![
            f64::from_bits(0xfff0_0000_0000_0001),
            -0.0,
            0.0,
            f64::NEG_INFINITY,
        ]);
        parity(&Vec::<u8>::new());
        parity(&Vec::<f32>::new());
    }
    struct DirectOnly;
    impl CandidType for DirectOnly {
        fn _ty() -> Type {
            TypeInner::Nat64.into()
        }
        fn idl_serialize<S: Serializer>(&self, serializer: S) -> Result<(), S::Error> {
            assert!(
                !core::any::type_name::<S>().contains("candid::ser::"),
                "ordinary serializer invoked"
            );
            serializer.serialize_nat64(42)
        }
    }
    #[test]
    fn typed_public_apis_do_not_invoke_the_ordinary_serializer() {
        let one = candidx::encode_one(DirectOnly).unwrap();
        assert_eq!(candid::decode_one::<u64>(&one).unwrap(), 42);
        assert_eq!(candidx::encode_args((DirectOnly,)).unwrap(), one);
        assert_eq!(
            candidx::encode_one_with_type(&DirectOnly, &TypeEnv::new(), &TypeInner::Nat64.into())
                .unwrap(),
            one
        );
        assert_eq!(
            candidx::encode_args_with_types(
                &[DirectOnly],
                &TypeEnv::new(),
                &[TypeInner::Nat64.into()]
            )
            .unwrap(),
            one
        );
    }
    struct FlipOpt(Cell<bool>);
    impl CandidType for FlipOpt {
        fn _ty() -> Type {
            TypeInner::Record(vec![Field {
                id: Label::Id(0).into(),
                ty: TypeInner::Opt(TypeInner::Nat8.into()).into(),
            }])
            .into()
        }
        fn idl_serialize<S: Serializer>(&self, serializer: S) -> Result<(), S::Error> {
            let mut record = serializer.serialize_struct()?;
            record.serialize_element(&if self.0.replace(false) {
                Some(7u8)
            } else {
                None
            })
        }
    }
    struct FlipVec(Cell<bool>);
    impl CandidType for FlipVec {
        fn _ty() -> Type {
            TypeInner::Vec(TypeInner::Nat64.into()).into()
        }
        fn idl_serialize<S: Serializer>(&self, serializer: S) -> Result<(), S::Error> {
            if self.0.replace(false) {
                vec![7u64].idl_serialize(serializer)
            } else {
                Vec::<u64>::new().idl_serialize(serializer)
            }
        }
    }
    #[test]
    fn changing_callback_shapes_are_rejected_without_returning_unused_types() {
        assert!(candidx::encode_one(FlipOpt(Cell::new(true)))
            .unwrap_err()
            .0
            .contains("shape changed"));
        assert!(candidx::encode_one(FlipVec(Cell::new(true)))
            .unwrap_err()
            .0
            .contains("shape changed"));
    }
    struct BadFlat;
    impl CandidType for BadFlat {
        fn _ty() -> Type {
            TypeInner::Variant(vec![Field {
                id: Label::Id(0).into(),
                ty: TypeInner::Record(vec![
                    Field {
                        id: Label::Id(2).into(),
                        ty: TypeInner::Nat8.into(),
                    },
                    Field {
                        id: Label::Id(1).into(),
                        ty: TypeInner::Nat8.into(),
                    },
                ])
                .into(),
            }])
            .into()
        }
        fn idl_serialize<S: Serializer>(&self, serializer: S) -> Result<(), S::Error> {
            let mut case = serializer.serialize_variant(0)?;
            case.serialize_element(&1u8)?;
            case.serialize_element(&2u8)
        }
    }
    struct IgnoreWrong;
    impl CandidType for IgnoreWrong {
        fn _ty() -> Type {
            TypeInner::Bool.into()
        }
        fn idl_serialize<S: Serializer>(&self, serializer: S) -> Result<(), S::Error> {
            let _ = serializer.serialize_nat8(7);
            Ok(())
        }
    }
    struct IgnoreWrongField;
    impl CandidType for IgnoreWrongField {
        fn _ty() -> Type {
            TypeInner::Record(vec![Field {
                id: Label::Id(0).into(),
                ty: TypeInner::Nat8.into(),
            }])
            .into()
        }
        fn idl_serialize<S: Serializer>(&self, serializer: S) -> Result<(), S::Error> {
            let mut record = serializer.serialize_struct()?;
            let _ = record.serialize_element(&true);
            Ok(())
        }
    }
    struct IgnoreWrongElement(bool);
    impl CandidType for IgnoreWrongElement {
        fn _ty() -> Type {
            TypeInner::Nat8.into()
        }
        fn idl_serialize<S: Serializer>(&self, serializer: S) -> Result<(), S::Error> {
            if self.0 {
                let _ = serializer.serialize_bool(true);
                Ok(())
            } else {
                serializer.serialize_nat8(7)
            }
        }
    }
    struct IgnoreExtra;
    impl CandidType for IgnoreExtra {
        fn _ty() -> Type {
            TypeInner::Vec(TypeInner::Nat8.into()).into()
        }
        fn idl_serialize<S: Serializer>(&self, serializer: S) -> Result<(), S::Error> {
            let mut vector = serializer.serialize_vec(1)?;
            vector.serialize_element(&7u8)?;
            let _ = vector.serialize_element(&8u8);
            Ok(())
        }
    }
    struct ForgetCompound;
    impl CandidType for ForgetCompound {
        fn _ty() -> Type {
            TypeInner::Vec(TypeInner::Nat8.into()).into()
        }
        fn idl_serialize<S: Serializer>(&self, serializer: S) -> Result<(), S::Error> {
            core::mem::forget(serializer.serialize_vec(1)?);
            Ok(())
        }
    }
    #[test]
    fn reused_shape_buffers_cannot_hide_swallowed_callback_errors() {
        assert!(candidx::encode_one(vec![
            IgnoreWrongElement(false),
            IgnoreWrongElement(false),
            IgnoreWrongElement(true),
        ])
        .is_err());
        assert!(candidx::encode_one(IgnoreExtra).is_err());
        assert!(candidx::encode_one(ForgetCompound)
            .unwrap_err()
            .0
            .contains("unfinished"));
    }
    #[test]
    fn malformed_source_types_and_swallowed_errors_are_rejected() {
        assert!(candidx::encode_one(BadFlat).is_err());
        assert!(candidx::encode_one(IgnoreWrong).is_err());
        assert!(candidx::encode_one(IgnoreWrongField).is_err());
        assert!(
            candidx::encode_one_with_type(&0u8, &TypeEnv::new(), &TypeInner::Bool.into()).is_err()
        );
    }
}
