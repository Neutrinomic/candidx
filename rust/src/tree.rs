//! Direct typed-tree encoding for no_std and runtime integrations.
use super::*;

/// A typed Candid value. Arbitrary integers use a little-endian unsigned
/// magnitude, independent of Candid wire encoding. Leading zero bytes and
/// negative zero are accepted as equivalent numeric values.
#[derive(Clone, Debug)]
pub enum DirectValue<'a> {
    Null,
    Reserved,
    Bool(bool),
    Nat(Cow<'a, [u8]>),
    Int {
        negative: bool,
        magnitude: Cow<'a, [u8]>,
    },
    Nat8(u8),
    Nat16(u16),
    Nat32(u32),
    Nat64(u64),
    Int8(i8),
    Int16(i16),
    Int32(i32),
    Int64(i64),
    Float32(f32),
    Float64(f64),
    Text(&'a str),
    Principal(&'a [u8]),
    Blob(&'a [u8]),
    Option(Option<Box<DirectValue<'a>>>),
    /// `binary` records the source element kind even when the vector is empty.
    /// A binary vector must contain only Nat8 values and equals Blob.
    Vec {
        elements: Vec<DirectValue<'a>>,
        binary: bool,
    },
    /// Fields must have strictly increasing, unique numeric Candid labels.
    Record(Vec<DirectField<'a>>),
    Variant(u32, Box<DirectValue<'a>>),
}

/// Source record-field metadata; only an optional field containing None is
/// omitted. Explicit Null and Reserved fields remain in the canonical value.
#[derive(Clone, Debug)]
pub struct DirectField<'a> {
    pub id: u32,
    pub optional: bool,
    pub value: DirectValue<'a>,
}

pub(super) fn magnitude_bits(bytes: &[u8]) -> usize {
    match bytes.iter().rposition(|byte| *byte != 0) {
        None => 0,
        Some(last) => last * 8 + (8 - bytes[last].leading_zeros() as usize),
    }
}
pub(super) fn magnitude_leb_len(bytes: &[u8], signed: bool, negative: bool) -> Result<usize> {
    let mut bits = magnitude_bits(bytes);
    if bits > 4096 * 7 + 1 {
        return bad("integer length limit");
    }
    if signed && negative && bits != 0 && bytes.iter().map(|b| b.count_ones()).sum::<u32>() == 1 {
        bits -= 1;
    }
    let length = (bits + usize::from(signed)).div_ceil(7).max(1);
    if length > 4096 {
        return bad("integer length limit");
    }
    Ok(length)
}
pub(super) fn emit_magnitude(
    w: &mut Writer,
    bytes: &[u8],
    signed: bool,
    negative: bool,
) -> Result<()> {
    let negative = negative && magnitude_bits(bytes) != 0;
    let length = magnitude_leb_len(bytes, signed, negative)?;
    let mut borrow = u8::from(negative);
    for group in 0..length {
        let offset = group * 7;
        let index = offset / 8;
        let shift = offset % 8;
        let low = u16::from(bytes.get(index).copied().unwrap_or(0));
        let high = u16::from(bytes.get(index + 1).copied().unwrap_or(0));
        let mut chunk = (((low | high << 8) >> shift) & 127) as u8;
        if negative {
            let (next, underflow) = chunk.overflowing_sub(borrow);
            borrow = u8::from(underflow);
            chunk = 127 ^ (next & 127);
        }
        w.add(&[chunk | if group + 1 < length { 128 } else { 0 }])?;
    }
    Ok(())
}

#[derive(Default)]
struct Budget {
    steps: usize,
    bytes: usize,
}
impl Budget {
    fn visit(&mut self, depth: usize) -> Result<()> {
        if depth > MAX_DEPTH {
            return bad("value nesting limit");
        }
        self.steps += 1;
        if self.steps > MAX_ITEMS {
            return bad("value traversal limit");
        }
        Ok(())
    }
    fn bytes(&mut self, n: usize) {
        self.bytes = self.bytes.saturating_add(n).min(MAX_BYTES);
    }
    fn binary(&mut self, n: usize, depth: usize) -> Result<()> {
        self.steps = self.steps.saturating_add(n);
        if self.steps > MAX_ITEMS || (n != 0 && depth + 1 > MAX_DEPTH) {
            return bad("value traversal limit");
        }
        Ok(())
    }
}
fn length(n: usize) -> Result<()> {
    if n > MAX_ITEMS {
        bad("collection length limit")
    } else {
        Ok(())
    }
}
fn absent(value: &DirectValue<'_>) -> bool {
    matches!(value, DirectValue::Null | DirectValue::Option(None))
}
fn derive(value: &DirectValue<'_>, depth: usize, budget: &mut Budget) -> Result<Shape> {
    budget.visit(depth)?;
    use DirectValue::*;
    let (op, size) = match value {
        Null | Option(None) => (-1, 0),
        Reserved => (-16, 0),
        Bool(_) => (-2, 1),
        Nat(magnitude) => (-3, magnitude_leb_len(magnitude, false, false)?),
        Int {
            negative,
            magnitude,
        } => (-4, magnitude_leb_len(magnitude, true, *negative)?),
        Nat8(_) => (-5, 1),
        Nat16(_) => (-6, 2),
        Nat32(_) => (-7, 4),
        Nat64(_) => (-8, 8),
        Int8(_) => (-9, 1),
        Int16(_) => (-10, 2),
        Int32(_) => (-11, 4),
        Int64(_) => (-12, 8),
        Float32(_) => (-13, 4),
        Float64(_) => (-14, 8),
        Text(text) => {
            length(text.len())?;
            (-15, text.len().saturating_add(10))
        }
        Principal(bytes) => {
            if bytes.len() > 29 {
                return bad("principal length exceeds 29");
            }
            (-24, bytes.len() + 2)
        }
        Blob(bytes) => {
            length(bytes.len())?;
            budget.bytes(bytes.len().saturating_add(10));
            budget.binary(bytes.len(), depth)?;
            return Ok(unary(-19, scalar(-5)));
        }
        Option(Some(child)) => {
            budget.bytes(1);
            return Ok(unary(-18, derive(child, depth + 1, budget)?));
        }
        Vec { elements, binary } => {
            length(elements.len())?;
            budget.bytes(10);
            if *binary {
                if !elements.iter().all(|v| matches!(v, Nat8(_))) {
                    return bad("binary vector requires Nat8 values");
                }
                budget.binary(elements.len(), depth)?;
                budget.bytes(elements.len());
                return Ok(unary(-19, scalar(-5)));
            }
            let mut child = scalar(-17);
            for item in elements {
                join(&mut child, derive(item, depth + 1, budget)?, 0)?;
            }
            return Ok(unary(-19, child));
        }
        Record(fields) => {
            length(fields.len())?;
            let mut result = Shape {
                op: -20,
                labels: vec![],
                children: vec![],
            };
            let mut previous = None;
            for field in fields {
                if previous.is_some_and(|id| id >= field.id) {
                    return bad("unordered field IDs");
                }
                previous = Some(field.id);
                if field.optional && !matches!(field.value, Option(_)) {
                    return bad("optional field requires an option value");
                }
                let child = derive(&field.value, depth + 1, budget)?;
                if !(field.optional && matches!(field.value, Option(None))) {
                    result.labels.push(field.id);
                    result.children.push(child);
                }
            }
            return Ok(result);
        }
        Variant(label, child) => {
            budget.bytes(10);
            return Ok(Shape {
                op: -21,
                labels: vec![*label],
                children: vec![derive(child, depth + 1, budget)?],
            });
        }
    };
    budget.bytes(size);
    Ok(scalar(op))
}
fn missing(w: &mut Writer, typ: &Shape) -> Result<()> {
    match typ.op {
        -1 => Ok(()),
        -18 => w.add(&[0]),
        _ => bad("missing required derived field"),
    }
}
pub(super) fn emit_tree(
    w: &mut Writer,
    value: &DirectValue<'_>,
    typ: &Shape,
    depth: usize,
) -> Result<()> {
    if depth > MAX_DEPTH {
        return bad("emitted value nesting limit");
    }
    use DirectValue::*;
    if typ.op == -18 {
        if absent(value) {
            return w.add(&[0]);
        }
        w.add(&[1])?;
        return emit_tree(
            w,
            match value {
                Option(Some(v)) => v,
                _ => value,
            },
            &typ.children[0],
            depth + 1,
        );
    }
    match value {
        Null | Option(None) if typ.op == -1 => Ok(()),
        Reserved if typ.op == -16 => Ok(()),
        Bool(v) if typ.op == -2 => w.add(&[u8::from(*v)]),
        Nat(v) if typ.op == -3 => emit_magnitude(w, v, false, false),
        Int {
            negative,
            magnitude,
        } if typ.op == -4 => emit_magnitude(w, magnitude, true, *negative),
        Nat8(v) if typ.op == -5 => w.add(&v.to_le_bytes()),
        Nat16(v) if typ.op == -6 => w.add(&v.to_le_bytes()),
        Nat32(v) if typ.op == -7 => w.add(&v.to_le_bytes()),
        Nat64(v) if typ.op == -8 => w.add(&v.to_le_bytes()),
        Int8(v) if typ.op == -9 => w.add(&v.to_le_bytes()),
        Int16(v) if typ.op == -10 => w.add(&v.to_le_bytes()),
        Int32(v) if typ.op == -11 => w.add(&v.to_le_bytes()),
        Int64(v) if typ.op == -12 => w.add(&v.to_le_bytes()),
        Float32(v) if typ.op == -13 => w.add(
            &if v.is_nan() {
                0x7fc0_0000u32
            } else {
                v.to_bits()
            }
            .to_le_bytes(),
        ),
        Float64(v) if typ.op == -14 => w.add(
            &if v.is_nan() {
                0x7ff8_0000_0000_0000u64
            } else {
                v.to_bits()
            }
            .to_le_bytes(),
        ),
        Text(v) if typ.op == -15 => {
            w.integer(v.len(), false)?;
            w.add(v.as_bytes())
        }
        Principal(v) if typ.op == -24 => {
            w.add(&[1, v.len() as u8])?;
            w.add(v)
        }
        Blob(v) if typ.op == -19 && typ.children[0].op == -5 => {
            w.integer(v.len(), false)?;
            w.add(v)
        }
        Vec { elements, .. } if typ.op == -19 => {
            w.integer(elements.len(), false)?;
            for child in elements {
                emit_tree(w, child, &typ.children[0], depth + 1)?;
            }
            Ok(())
        }
        Record(fields) if typ.op == -20 => {
            for (id, child_type) in typ.labels.iter().zip(&typ.children) {
                match fields.binary_search_by_key(id, |field| field.id) {
                    Ok(index) => emit_tree(w, &fields[index].value, child_type, depth + 1)?,
                    Err(_) => missing(w, child_type)?,
                }
            }
            Ok(())
        }
        Variant(id, child) if typ.op == -21 => {
            let index = typ
                .labels
                .binary_search(id)
                .map_err(|_| Error("missing derived variant".into()))?;
            w.integer(index, false)?;
            emit_tree(w, child, &typ.children[index], depth + 1)
        }
        _ => bad("cannot emit at derived type"),
    }
}

/// Encode typed values directly. The first visit derives their used shapes;
/// the second writes the canonical header and payload. No ordinary message is
/// constructed or parsed. Values must remain unchanged during this call.
/// Each vector must originate from one uniform source element schema. Retained
/// record-field presence can differ only through absent optional fields;
/// selected variant cases can differ. Arbitrary heterogeneous record schemas
/// are outside this typed-value contract.
pub fn encode_direct_args(values: &[DirectValue<'_>]) -> Result<Vec<u8>> {
    length(values.len())?;
    let mut budget = Budget::default();
    let types = values
        .iter()
        .map(|v| derive(v, 0, &mut budget))
        .collect::<Result<Vec<_>>>()?;
    let mut writer = Writer(
        Vec::with_capacity(budget.bytes.saturating_add(128).min(MAX_BYTES)),
        false,
    );
    write_header(&mut writer, &types)?;
    for (value, typ) in values.iter().zip(&types) {
        emit_tree(&mut writer, value, typ, 0)?;
    }
    Ok(writer.0)
}
