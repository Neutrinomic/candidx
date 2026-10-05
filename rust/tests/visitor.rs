use candidx::{
    encode_direct_args, encode_source_args, DirectField, DirectSource, DirectValue as V, Error,
    SourceField,
};
use std::borrow::Cow;
use std::cell::Cell;

type Result<T> = std::result::Result<T, Error>;
fn invalid<T>() -> Result<T> {
    Err(Error("wrong source method".into()))
}

// This adapter borrows an existing test tree; the encoder under test never
// constructs one. Binary arrays use element handles and blobs borrow a span.
struct TreeSource<'a> {
    roots: &'a [V<'a>],
    root_visits: Cell<usize>,
    scalar_visits: Cell<usize>,
}
impl<'a> TreeSource<'a> {
    fn new(roots: &'a [V<'a>]) -> Self {
        Self {
            roots,
            root_visits: Cell::new(0),
            scalar_visits: Cell::new(0),
        }
    }
}
impl<'a> DirectSource for TreeSource<'a> {
    type Value = &'a V<'a>;

    fn kind(&self, value: Self::Value) -> Result<i32> {
        if self
            .roots
            .first()
            .is_some_and(|root| std::ptr::eq(root, value))
        {
            self.root_visits.set(self.root_visits.get() + 1);
        }
        Ok(match value {
            V::Null => -1,
            V::Reserved => -16,
            V::Bool(_) => -2,
            V::Nat(_) => -3,
            V::Int { .. } => -4,
            V::Nat8(_) => -5,
            V::Nat16(_) => -6,
            V::Nat32(_) => -7,
            V::Nat64(_) => -8,
            V::Int8(_) => -9,
            V::Int16(_) => -10,
            V::Int32(_) => -11,
            V::Int64(_) => -12,
            V::Float32(_) => -13,
            V::Float64(_) => -14,
            V::Text(_) => -15,
            V::Principal(_) => -24,
            V::Option(_) => -18,
            V::Blob(_) | V::Vec { .. } => -19,
            V::Record(_) => -20,
            V::Variant(_, _) => -21,
        })
    }
    fn scalar(&self, value: Self::Value) -> Result<V<'_>> {
        assert_eq!(
            self.root_visits.get(),
            2,
            "scalar read before emission or a third source visit"
        );
        self.scalar_visits.set(self.scalar_visits.get() + 1);
        Ok(match value {
            V::Null => V::Null,
            V::Reserved => V::Reserved,
            V::Bool(v) => V::Bool(*v),
            V::Nat(v) => V::Nat(Cow::Borrowed(v)),
            V::Int {
                negative,
                magnitude,
            } => V::Int {
                negative: *negative,
                magnitude: Cow::Borrowed(magnitude),
            },
            V::Nat8(v) => V::Nat8(*v),
            V::Nat16(v) => V::Nat16(*v),
            V::Nat32(v) => V::Nat32(*v),
            V::Nat64(v) => V::Nat64(*v),
            V::Int8(v) => V::Int8(*v),
            V::Int16(v) => V::Int16(*v),
            V::Int32(v) => V::Int32(*v),
            V::Int64(v) => V::Int64(*v),
            V::Float32(v) => V::Float32(*v),
            V::Float64(v) => V::Float64(*v),
            V::Text(v) => V::Text(v),
            V::Principal(v) => V::Principal(v),
            _ => return invalid(),
        })
    }
    fn scalar_size_hint(&self, value: Self::Value) -> Result<usize> {
        Ok(match value {
            V::Nat(bytes)
            | V::Int {
                magnitude: bytes, ..
            } => bytes.len().saturating_mul(8).div_ceil(7) + 1,
            V::Text(text) => text.len() + 10,
            V::Principal(bytes) => bytes.len() + 2,
            _ => 0,
        })
    }
    fn option(&self, value: Self::Value) -> Result<Option<Self::Value>> {
        match value {
            V::Option(child) => Ok(child.as_deref()),
            _ => invalid(),
        }
    }
    fn vector_len(&self, value: Self::Value) -> Result<usize> {
        match value {
            V::Blob(bytes) => Ok(bytes.len()),
            V::Vec { elements, .. } => Ok(elements.len()),
            _ => invalid(),
        }
    }
    fn vector_element(&self, value: Self::Value, index: usize) -> Result<Self::Value> {
        match value {
            V::Vec { elements, .. } => elements
                .get(index)
                .ok_or_else(|| Error("element index".into())),
            V::Blob(_) => panic!("borrowed blob was expanded into element handles"),
            _ => invalid(),
        }
    }
    fn is_binary(&self, value: Self::Value) -> Result<bool> {
        match value {
            V::Blob(_) => Ok(true),
            V::Vec { binary, .. } => Ok(*binary),
            _ => invalid(),
        }
    }
    fn binary_slice(&self, value: Self::Value) -> Result<Option<&[u8]>> {
        match value {
            V::Blob(bytes) => Ok(Some(bytes)),
            V::Vec { binary: true, .. } => Ok(None),
            _ => invalid(),
        }
    }
    fn record_len(&self, value: Self::Value) -> Result<usize> {
        match value {
            V::Record(fields) => Ok(fields.len()),
            _ => invalid(),
        }
    }
    fn record_field(&self, value: Self::Value, index: usize) -> Result<SourceField<Self::Value>> {
        match value {
            V::Record(fields) => {
                let field = fields
                    .get(index)
                    .ok_or_else(|| Error("field index".into()))?;
                Ok(SourceField {
                    id: field.id,
                    optional: field.optional,
                    value: &field.value,
                })
            }
            _ => invalid(),
        }
    }
    fn variant(&self, value: Self::Value) -> Result<(u32, Self::Value)> {
        match value {
            V::Variant(id, child) => Ok((*id, child)),
            _ => invalid(),
        }
    }
}

fn source_encode(values: &[V<'_>]) -> Result<Vec<u8>> {
    let source = TreeSource::new(values);
    let roots: Vec<_> = values.iter().collect();
    let result = encode_source_args(&source, &roots);
    if result.is_ok() && !values.is_empty() {
        assert_eq!(source.root_visits.get(), 2);
    }
    result
}
fn parity(values: &[V<'_>]) -> Vec<u8> {
    let actual = source_encode(values).unwrap();
    assert_eq!(actual, encode_direct_args(values).unwrap());
    assert_eq!(candidx::canonicalize(&actual).unwrap(), actual);
    actual
}
fn field(id: u32, optional: bool, value: V<'_>) -> DirectField<'_> {
    DirectField {
        id,
        optional,
        value,
    }
}
fn some(value: V<'_>) -> V<'_> {
    V::Option(Some(Box::new(value)))
}

#[test]
fn native_source_matches_frozen_scalar_bytes_and_preserves_scalar_kinds() {
    assert_eq!(
        hex::encode(parity(&[V::Nat(Cow::Borrowed(&[128, 0]))])),
        "4449444c00017d8001"
    );
    parity(&[
        V::Null,
        V::Reserved,
        V::Bool(true),
        V::Nat(Cow::Borrowed(&[0, 0])),
        V::Int {
            negative: true,
            magnitude: Cow::Borrowed(&[64]),
        },
        V::Int {
            negative: true,
            magnitude: Cow::Borrowed(&[]),
        },
        V::Nat8(255),
        V::Nat16(u16::MAX),
        V::Nat32(u32::MAX),
        V::Nat64(u64::MAX),
        V::Int8(i8::MIN),
        V::Int16(i16::MIN),
        V::Int32(i32::MIN),
        V::Int64(i64::MIN),
        V::Float32(f32::from_bits(0xff80_0001)),
        V::Float32(-0.0),
        V::Float64(f64::from_bits(0xfff0_0000_0000_0001)),
        V::Float64(-0.0),
        V::Text("\u{0}é🦀"),
        V::Principal(&[0, 1, 2]),
    ]);
    assert_eq!(source_encode(&[]).unwrap(), b"DIDL\0\0");
}

#[test]
fn native_source_retains_binary_kind_and_borrows_blob_spans() {
    let bytes = [0, 255, 1];
    let blob = parity(&[V::Blob(&bytes)]);
    assert_eq!(hex::encode(&blob), "4449444c016d7b01000300ff01");
    assert_eq!(
        blob,
        parity(&[V::Vec {
            elements: bytes.iter().map(|byte| V::Nat8(*byte)).collect(),
            binary: true
        }])
    );
    let empty_blob = parity(&[V::Blob(&[])]);
    assert_eq!(
        empty_blob,
        parity(&[V::Vec {
            elements: vec![],
            binary: true
        }])
    );
    assert_ne!(
        empty_blob,
        parity(&[V::Vec {
            elements: vec![],
            binary: false
        }])
    );
}

#[test]
fn native_source_omits_only_absent_optional_fields() {
    let bytes = parity(&[V::Record(vec![
        field(0, false, V::Null),
        field(1, false, V::Reserved),
        field(2, true, V::Option(None)),
        field(3, true, some(V::Option(None))),
    ])]);
    assert_eq!(hex::encode(bytes), "4449444c026c03007f017003016e7f010001");
    parity(&[V::Vec {
        elements: vec![
            V::Option(None),
            some(V::Option(None)),
            some(some(V::Nat8(7))),
        ],
        binary: false,
    }]);
}

#[test]
fn native_source_joins_used_record_fields_and_reindexes_selected_variants() {
    let records = V::Vec {
        binary: false,
        elements: vec![
            V::Record(vec![
                field(0, false, V::Nat8(1)),
                field(1, true, V::Option(None)),
                field(2, false, V::Variant(91, Box::new(V::Null))),
            ]),
            V::Record(vec![
                field(0, false, V::Nat8(2)),
                field(1, true, some(V::Text("x"))),
                field(2, false, V::Variant(3, Box::new(V::Bool(true)))),
            ]),
            V::Record(vec![
                field(0, false, V::Nat8(3)),
                field(1, true, V::Option(None)),
                field(2, false, V::Variant(91, Box::new(V::Null))),
            ]),
        ],
    };
    parity(&[records]);
    parity(&[V::Vec {
        elements: vec![
            V::Vec {
                elements: vec![],
                binary: false,
            },
            V::Vec {
                elements: vec![V::Nat16(5)],
                binary: false,
            },
        ],
        binary: false,
    }]);
}

#[test]
fn native_source_shape_reuse_preserves_nested_joins() {
    let records = (0u32..512)
        .map(|index| {
            let tag = if index % 5 == 0 {
                V::Variant(7, Box::new(V::Null))
            } else {
                V::Variant(
                    1,
                    Box::new(V::Record(vec![
                        field(
                            0,
                            true,
                            if index % 2 == 0 {
                                some(V::Bool(true))
                            } else {
                                V::Option(None)
                            },
                        ),
                        field(
                            1,
                            true,
                            if index % 3 == 0 {
                                some(V::Text("value"))
                            } else {
                                V::Option(None)
                            },
                        ),
                    ])),
                )
            };
            V::Record(vec![
                field(0, false, V::Principal(&[1, 2])),
                field(
                    1,
                    true,
                    if index % 3 == 0 {
                        some(V::Blob(&[]))
                    } else {
                        V::Option(None)
                    },
                ),
                field(2, false, V::Nat(Cow::Owned(index.to_le_bytes().to_vec()))),
                field(3, false, tag),
                field(
                    4,
                    false,
                    V::Vec {
                        elements: (0..index % 4).map(|i| V::Nat16(i as u16)).collect(),
                        binary: false,
                    },
                ),
            ])
        })
        .collect();
    parity(&[V::Vec {
        elements: records,
        binary: false,
    }]);
}

#[test]
fn native_source_rejects_malformed_metadata_and_scalar_limits() {
    assert!(source_encode(&[V::Record(vec![
        field(2, false, V::Null),
        field(1, false, V::Null)
    ])])
    .is_err());
    assert!(source_encode(&[V::Record(vec![field(0, true, V::Null)])]).is_err());
    assert!(source_encode(&[V::Vec {
        elements: vec![V::Nat16(1)],
        binary: true
    }])
    .is_err());
    assert!(source_encode(&[V::Principal(&[0; 30])]).is_err());
    let mut excessive = vec![0; 3585];
    excessive[3584] = 1;
    assert!(source_encode(&[V::Nat(Cow::Borrowed(&excessive))]).is_err());
    let accepted = vec![255; 3584];
    parity(&[V::Nat(Cow::Borrowed(&accepted))]);
    let mut nested = V::Blob(&[1]);
    for _ in 0..256 {
        nested = some(nested);
    }
    assert!(source_encode(&[nested]).is_err());
    let roots: Vec<_> = (0..1025)
        .map(|id| V::Record(vec![field(id, false, V::Null)]))
        .collect();
    assert!(source_encode(&roots).is_err());
    parity(&roots[..1024]);
}

#[test]
fn native_source_keeps_empty_vector_and_binary_depth_boundaries() {
    for mut value in [
        V::Blob(&[]),
        V::Vec {
            elements: vec![],
            binary: false,
        },
    ] {
        for _ in 0..256 {
            value = some(value);
        }
        parity(&[value]);
    }
    let bytes = vec![0; 2_000_000];
    assert!(source_encode(&[V::Blob(&bytes)]).is_err());
    parity(&[V::Blob(&bytes[..1_999_999])]);
}

// No scalar nodes exist in this source. It also checks that a changed second
// visit cannot evade the collection or binary-element traversal budgets.
struct BinarySource {
    bytes: Vec<u8>,
    first_len: usize,
    second_len: usize,
    visits: Cell<usize>,
}
impl DirectSource for BinarySource {
    type Value = ();
    fn kind(&self, _: ()) -> Result<i32> {
        self.visits.set(self.visits.get() + 1);
        Ok(-19)
    }
    fn scalar(&self, _: ()) -> Result<V<'_>> {
        panic!("binary span read as a scalar")
    }
    fn option(&self, _: ()) -> Result<Option<()>> {
        invalid()
    }
    fn vector_len(&self, _: ()) -> Result<usize> {
        Ok(if self.visits.get() == 1 {
            self.first_len
        } else {
            self.second_len
        })
    }
    fn vector_element(&self, _: (), _: usize) -> Result<()> {
        panic!("binary span expanded")
    }
    fn is_binary(&self, _: ()) -> Result<bool> {
        Ok(true)
    }
    fn binary_slice(&self, _: ()) -> Result<Option<&[u8]>> {
        Ok(Some(&self.bytes))
    }
    fn record_len(&self, _: ()) -> Result<usize> {
        invalid()
    }
    fn record_field(&self, _: (), _: usize) -> Result<SourceField<()>> {
        invalid()
    }
    fn variant(&self, _: ()) -> Result<(u32, ())> {
        invalid()
    }
}
#[test]
fn native_source_enforces_steps_lengths_and_output_bytes_on_emission() {
    let source = BinarySource {
        bytes: vec![0; 2_000_000],
        first_len: 1,
        second_len: 2_000_000,
        visits: Cell::new(0),
    };
    assert!(encode_source_args(&source, &[()])
        .unwrap_err()
        .0
        .contains("traversal limit"));
    let source = BinarySource {
        bytes: vec![],
        first_len: 2_000_001,
        second_len: 0,
        visits: Cell::new(0),
    };
    assert!(encode_source_args(&source, &[()])
        .unwrap_err()
        .0
        .contains("collection length limit"));
    let source = BinarySource {
        bytes: vec![1; 2],
        first_len: 1,
        second_len: 1,
        visits: Cell::new(0),
    };
    assert!(encode_source_args(&source, &[()])
        .unwrap_err()
        .0
        .contains("slice length"));
    let text = "x".repeat(2_000_000);
    let values: Vec<_> = (0..17).map(|_| V::Text(&text)).collect();
    assert!(source_encode(&values)
        .unwrap_err()
        .0
        .contains("output byte limit"));
}

struct ChangingSource {
    visits: Cell<usize>,
    optional_field: bool,
}
impl DirectSource for ChangingSource {
    type Value = u8;
    fn kind(&self, value: u8) -> Result<i32> {
        match value {
            0 => {
                self.visits.set(self.visits.get() + 1);
                Ok(if self.optional_field { -20 } else { -19 })
            }
            1 => Ok(-18),
            2 => Ok(-1),
            _ => invalid(),
        }
    }
    fn scalar(&self, _: u8) -> Result<V<'_>> {
        Ok(V::Null)
    }
    fn option(&self, _: u8) -> Result<Option<u8>> {
        Ok(if self.optional_field && self.visits.get() == 1 {
            Some(2)
        } else {
            None
        })
    }
    fn vector_len(&self, _: u8) -> Result<usize> {
        Ok(if self.visits.get() == 1 { 1 } else { 0 })
    }
    fn vector_element(&self, _: u8, _: usize) -> Result<u8> {
        Ok(1)
    }
    fn is_binary(&self, _: u8) -> Result<bool> {
        Ok(false)
    }
    fn record_len(&self, _: u8) -> Result<usize> {
        Ok(1)
    }
    fn record_field(&self, _: u8, _: usize) -> Result<SourceField<u8>> {
        Ok(SourceField {
            id: 0,
            optional: true,
            value: 1,
        })
    }
    fn variant(&self, _: u8) -> Result<(u32, u8)> {
        invalid()
    }
}
#[test]
fn native_source_rejects_changed_used_shapes_without_a_third_visit() {
    // A vector becoming empty changes vec null to vec empty. An optional
    // field becoming absent must not leave an unused field in the header.
    for optional_field in [false, true] {
        let source = ChangingSource {
            visits: Cell::new(0),
            optional_field,
        };
        assert!(encode_source_args(&source, &[0])
            .unwrap_err()
            .0
            .contains("shape changed"));
        assert_eq!(source.visits.get(), 2);
    }
}

struct MisreportedScalar {
    compound: bool,
}
impl DirectSource for MisreportedScalar {
    type Value = ();
    fn kind(&self, _: ()) -> Result<i32> {
        Ok(-5)
    }
    fn scalar(&self, _: ()) -> Result<V<'_>> {
        Ok(if self.compound {
            V::Option(None)
        } else {
            V::Text("wrong kind")
        })
    }
    fn option(&self, _: ()) -> Result<Option<()>> {
        invalid()
    }
    fn vector_len(&self, _: ()) -> Result<usize> {
        invalid()
    }
    fn vector_element(&self, _: (), _: usize) -> Result<()> {
        invalid()
    }
    fn is_binary(&self, _: ()) -> Result<bool> {
        invalid()
    }
    fn record_len(&self, _: ()) -> Result<usize> {
        invalid()
    }
    fn record_field(&self, _: (), _: usize) -> Result<SourceField<()>> {
        invalid()
    }
    fn variant(&self, _: ()) -> Result<(u32, ())> {
        invalid()
    }
}
#[test]
fn native_source_validates_scalar_callback_kinds() {
    for compound in [false, true] {
        assert!(encode_source_args(&MisreportedScalar { compound }, &[()]).is_err());
    }
}
