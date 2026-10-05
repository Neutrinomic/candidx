//! Two visits of a native source, without materializing its payload tree.
use super::*;

/// A source record field, in increasing numeric Candid label order.
/// Only an optional field containing an absent option is omitted.
#[derive(Clone, Copy, Debug)]
pub struct SourceField<V> {
    pub id: u32,
    pub optional: bool,
    pub value: V,
}

/// Read-only access to native typed values. A handle can carry a source type,
/// its layout, and the native value; the encoder retains only used shapes.
///
/// `kind` uses Candid primitive/constructor opcodes: -1 through -16 for the
/// supported scalars, -18 for options, -19 for vectors/binary data, -20 for
/// records, -21 for variants, and -24 for public principals. `empty`, function,
/// service, and opaque reference values are unsupported.
///
/// Structural methods and `scalar_size_hint` are used during both visits or
/// the first visit. `scalar` and `binary_slice` are used only when emitting.
/// `scalar` must return a scalar variant of [`DirectValue`], matching `kind`;
/// its integer magnitudes can be borrowed or constructed at that point. Text
/// must be valid UTF-8. The source must remain unchanged during the call. The
/// encoder also checks that the observed used shapes agree between visits.
/// Elements of each vector must follow one supported source schema. Retained
/// record fields may differ through absent optional fields, and selected
/// variant cases may differ.
pub trait DirectSource {
    type Value: Copy;

    fn kind(&self, value: Self::Value) -> Result<i32>;
    fn scalar(&self, value: Self::Value) -> Result<DirectValue<'_>>;

    /// A cheap payload-size estimate used only to reserve output capacity.
    /// Fixed-size primitive scalars need no hint. A hint never changes bytes
    /// or validation, and should not construct a magnitude or flatten text.
    fn scalar_size_hint(&self, _value: Self::Value) -> Result<usize> {
        Ok(0)
    }

    fn option(&self, value: Self::Value) -> Result<Option<Self::Value>>;
    fn vector_len(&self, value: Self::Value) -> Result<usize>;
    fn vector_element(&self, value: Self::Value, index: usize) -> Result<Self::Value>;
    /// Whether the source vector is binary data (`vec nat8`), including empty
    /// binary vectors. Each element must be a Nat8 value.
    fn is_binary(&self, value: Self::Value) -> Result<bool>;
    /// Borrow a contiguous binary payload when available. Returning None
    /// makes the encoder read elements without creating per-byte tree nodes.
    fn binary_slice(&self, _value: Self::Value) -> Result<Option<&[u8]>> {
        Ok(None)
    }
    fn record_len(&self, value: Self::Value) -> Result<usize>;
    fn record_field(&self, value: Self::Value, index: usize) -> Result<SourceField<Self::Value>>;
    fn variant(&self, value: Self::Value) -> Result<(u32, Self::Value)>;
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
        self.steps = self.steps.saturating_add(1);
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
        if self.steps > MAX_ITEMS {
            return bad("value traversal limit");
        }
        if n != 0 && depth + 1 > MAX_DEPTH {
            return bad("value nesting limit");
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
fn scalar_kind(op: i32) -> bool {
    (-16..=-1).contains(&op) || op == -24
}
fn scalar_size<S: DirectSource + ?Sized>(source: &S, value: S::Value, op: i32) -> Result<usize> {
    Ok(match op {
        -1 | -16 => 0,
        -2 | -5 | -9 => 1,
        -6 | -10 => 2,
        -7 | -11 | -13 => 4,
        -8 | -12 | -14 => 8,
        _ => source.scalar_size_hint(value)?,
    })
}
pub(super) fn set_scalar(shape: &mut Shape, op: i32) {
    shape.op = op;
    shape.labels.clear();
    shape.children.clear();
}
pub(super) fn set_unary(shape: &mut Shape, op: i32) {
    shape.op = op;
    shape.labels.clear();
    shape.children.truncate(1);
    if shape.children.is_empty() {
        shape.children.reserve_exact(1);
        shape.children.push(scalar(-17));
    }
}
// Exactly the no-widening case of J(current, incoming). Keeping an already
// included scratch shape preserves its buffers for the next vector element.
fn already_joined(current: &Shape, incoming: &Shape, depth: usize) -> bool {
    if depth > MAX_DEPTH {
        return false;
    }
    if current == incoming || incoming.op == -17 {
        return true;
    }
    if current.op == -18 {
        return incoming.op == -1
            || already_joined(
                &current.children[0],
                if incoming.op == -18 {
                    &incoming.children[0]
                } else {
                    incoming
                },
                depth + 1,
            );
    }
    if current.op != incoming.op {
        return false;
    }
    if current.op == -19 {
        return already_joined(&current.children[0], &incoming.children[0], depth + 1);
    }
    if current.op == -20 {
        let mut next = 0;
        for (id, typ) in current.labels.iter().zip(&current.children) {
            if incoming.labels.get(next) == Some(id) {
                if !already_joined(typ, &incoming.children[next], depth + 1) {
                    return false;
                }
                next += 1;
            } else if ![-1, -18].contains(&typ.op) {
                return false;
            }
        }
        return next == incoming.labels.len();
    }
    if current.op == -21 {
        return incoming
            .labels
            .iter()
            .zip(&incoming.children)
            .all(|(id, child)| {
                current
                    .labels
                    .binary_search(id)
                    .is_ok_and(|index| already_joined(&current.children[index], child, depth + 1))
            });
    }
    false
}
pub(super) fn join_scratch(current: &mut Shape, scratch: &mut Shape) -> Result<()> {
    if !already_joined(current, scratch, 0) {
        join(current, core::mem::replace(scratch, scalar(-17)), 0)?;
    }
    Ok(())
}
fn derive<S: DirectSource + ?Sized>(
    source: &S,
    value: S::Value,
    depth: usize,
    budget: &mut Budget,
) -> Result<Shape> {
    let mut shape = scalar(-17);
    derive_into(source, value, depth, budget, &mut shape)?;
    Ok(shape)
}
fn derive_into<S: DirectSource + ?Sized>(
    source: &S,
    value: S::Value,
    depth: usize,
    budget: &mut Budget,
    result: &mut Shape,
) -> Result<i32> {
    budget.visit(depth)?;
    let kind = source.kind(value)?;
    match kind {
        -18 => match source.option(value)? {
            None => set_scalar(result, -1),
            Some(child) => {
                budget.bytes(1);
                set_unary(result, -18);
                derive_into(source, child, depth + 1, budget, &mut result.children[0])?;
            }
        },
        -19 => {
            let n = source.vector_len(value)?;
            length(n)?;
            budget.bytes(10);
            set_unary(result, -19);
            let child = &mut result.children[0];
            if source.is_binary(value)? {
                budget.binary(n, depth)?;
                budget.bytes(n);
                set_scalar(child, -5);
            } else if n == 0 {
                set_scalar(child, -17);
            } else {
                let first = source.vector_element(value, 0)?;
                derive_into(source, first, depth + 1, budget, child)?;
                let mut scratch = scalar(-17);
                for index in 1..n {
                    let element = source.vector_element(value, index)?;
                    derive_into(source, element, depth + 1, budget, &mut scratch)?;
                    join_scratch(child, &mut scratch)?;
                }
            }
        }
        -20 => {
            let n = source.record_len(value)?;
            length(n)?;
            result.op = -20;
            result.labels.clear();
            let mut retained = 0;
            let mut previous = None;
            for index in 0..n {
                let field = source.record_field(value, index)?;
                if previous.is_some_and(|id| id >= field.id) {
                    return bad("unordered field IDs");
                }
                previous = Some(field.id);
                let mut spare = scalar(-17);
                let child = result.children.get_mut(retained).unwrap_or(&mut spare);
                let child_kind = derive_into(source, field.value, depth + 1, budget, child)?;
                if field.optional && child_kind != -18 {
                    return bad("optional field requires an option value");
                }
                if !(field.optional && child.op == -1) {
                    result.labels.push(field.id);
                    if retained == result.children.len() {
                        result.children.push(spare);
                    }
                    retained += 1;
                }
            }
            result.children.truncate(retained);
        }
        -21 => {
            let (id, child) = source.variant(value)?;
            budget.bytes(10);
            set_unary(result, -21);
            result.labels.push(id);
            derive_into(source, child, depth + 1, budget, &mut result.children[0])?;
        }
        op if scalar_kind(op) => {
            budget.bytes(scalar_size(source, value, op)?);
            set_scalar(result, op);
        }
        _ => return bad("unsupported source value kind"),
    }
    Ok(kind)
}
fn validate_scalar(value: &DirectValue<'_>, kind: i32) -> Result<()> {
    use DirectValue::*;
    let op = match value {
        Null => -1,
        Reserved => -16,
        Bool(_) => -2,
        Nat(bytes) => {
            tree::magnitude_leb_len(bytes, false, false)?;
            -3
        }
        Int {
            negative,
            magnitude,
        } => {
            tree::magnitude_leb_len(magnitude, true, *negative)?;
            -4
        }
        Nat8(_) => -5,
        Nat16(_) => -6,
        Nat32(_) => -7,
        Nat64(_) => -8,
        Int8(_) => -9,
        Int16(_) => -10,
        Int32(_) => -11,
        Int64(_) => -12,
        Float32(_) => -13,
        Float64(_) => -14,
        Text(text) => {
            length(text.len())?;
            -15
        }
        Principal(bytes) => {
            if bytes.len() > 29 {
                return bad("principal length exceeds 29");
            }
            -24
        }
        _ => return bad("source scalar method returned a compound value"),
    };
    if op != kind {
        return bad("source scalar disagrees with its kind");
    }
    Ok(())
}
fn present<'a>(
    writer: &mut Writer,
    mut typ: &'a Shape,
    kind: i32,
    mut depth: usize,
) -> Result<(&'a Shape, usize)> {
    while typ.op == -18 {
        writer.add(&[1])?;
        typ = &typ.children[0];
        depth += 1;
        if depth > MAX_DEPTH {
            return bad("emitted value nesting limit");
        }
    }
    if typ.op != kind {
        return bad("source value changed between visits");
    }
    Ok((typ, depth))
}
fn missing(writer: &mut Writer, typ: &Shape) -> Result<()> {
    match typ.op {
        -1 => Ok(()),
        -18 => writer.add(&[0]),
        _ => bad("missing required derived field"),
    }
}
fn absent_field<S: DirectSource + ?Sized>(
    source: &S,
    field: SourceField<S::Value>,
    depth: usize,
    budget: &mut Budget,
) -> Result<()> {
    budget.visit(depth)?;
    if !field.optional || source.kind(field.value)? != -18 || source.option(field.value)?.is_some()
    {
        return bad("source field changed between visits");
    }
    Ok(())
}
#[derive(Clone, Copy)]
struct Depth {
    source: usize,
    wire: usize,
}
fn emit_into<S: DirectSource + ?Sized>(
    source: &S,
    value: S::Value,
    typ: &Shape,
    depth: Depth,
    writer: &mut Writer,
    budget: &mut Budget,
    result: &mut Shape,
) -> Result<i32> {
    budget.visit(depth.source)?;
    if depth.wire > MAX_DEPTH {
        return bad("emitted value nesting limit");
    }
    let kind = source.kind(value)?;
    match kind {
        -18 => match source.option(value)? {
            None => {
                tree::emit_tree(writer, &DirectValue::Null, typ, depth.wire)?;
                set_scalar(result, -1);
            }
            Some(child) => {
                if typ.op != -18 {
                    return bad("source option changed between visits");
                }
                writer.add(&[1])?;
                set_unary(result, -18);
                emit_into(
                    source,
                    child,
                    &typ.children[0],
                    Depth {
                        source: depth.source + 1,
                        wire: depth.wire + 1,
                    },
                    writer,
                    budget,
                    &mut result.children[0],
                )?;
            }
        },
        -19 => {
            let (typ, wire_depth) = present(writer, typ, kind, depth.wire)?;
            let n = source.vector_len(value)?;
            length(n)?;
            writer.integer(n, false)?;
            let child_type = &typ.children[0];
            set_unary(result, -19);
            let child = &mut result.children[0];
            let child_depth = Depth {
                source: depth.source + 1,
                wire: wire_depth + 1,
            };
            if source.is_binary(value)? {
                if let Some(bytes) = source.binary_slice(value)? {
                    if bytes.len() != n {
                        return bad("binary slice length disagrees with vector length");
                    }
                    budget.binary(n, depth.source)?;
                    if n != 0 && wire_depth + 1 > MAX_DEPTH {
                        return bad("emitted value nesting limit");
                    }
                    if child_type.op == -5 {
                        writer.add(bytes)?;
                    } else {
                        for byte in bytes {
                            tree::emit_tree(
                                writer,
                                &DirectValue::Nat8(*byte),
                                child_type,
                                wire_depth + 1,
                            )?;
                        }
                    }
                } else {
                    let mut scratch = scalar(-17);
                    for index in 0..n {
                        let element = source.vector_element(value, index)?;
                        let child_kind = emit_into(
                            source,
                            element,
                            child_type,
                            child_depth,
                            writer,
                            budget,
                            &mut scratch,
                        )?;
                        if child_kind != -5 {
                            return bad("binary vector requires Nat8 values");
                        }
                    }
                }
                set_scalar(child, -5);
            } else if n == 0 {
                set_scalar(child, -17);
            } else {
                let first = source.vector_element(value, 0)?;
                emit_into(
                    source,
                    first,
                    child_type,
                    child_depth,
                    writer,
                    budget,
                    child,
                )?;
                let mut scratch = scalar(-17);
                for index in 1..n {
                    let element = source.vector_element(value, index)?;
                    emit_into(
                        source,
                        element,
                        child_type,
                        child_depth,
                        writer,
                        budget,
                        &mut scratch,
                    )?;
                    join_scratch(child, &mut scratch)?;
                }
            }
        }
        -20 => {
            let (typ, wire_depth) = present(writer, typ, kind, depth.wire)?;
            let n = source.record_len(value)?;
            length(n)?;
            result.op = -20;
            result.labels.clear();
            let mut retained = 0;
            let mut previous = None;
            let mut target_index = 0;
            for index in 0..n {
                let field = source.record_field(value, index)?;
                if previous.is_some_and(|id| id >= field.id) {
                    return bad("unordered field IDs");
                }
                previous = Some(field.id);
                while typ
                    .labels
                    .get(target_index)
                    .is_some_and(|id| *id < field.id)
                {
                    missing(writer, &typ.children[target_index])?;
                    target_index += 1;
                }
                if typ.labels.get(target_index) == Some(&field.id) {
                    let mut spare = scalar(-17);
                    let child = result.children.get_mut(retained).unwrap_or(&mut spare);
                    let child_kind = emit_into(
                        source,
                        field.value,
                        &typ.children[target_index],
                        Depth {
                            source: depth.source + 1,
                            wire: wire_depth + 1,
                        },
                        writer,
                        budget,
                        child,
                    )?;
                    if field.optional && child_kind != -18 {
                        return bad("optional field requires an option value");
                    }
                    if !(field.optional && child.op == -1) {
                        result.labels.push(field.id);
                        if retained == result.children.len() {
                            result.children.push(spare);
                        }
                        retained += 1;
                    }
                    target_index += 1;
                } else {
                    absent_field(source, field, depth.source + 1, budget)?;
                }
            }
            for child in &typ.children[target_index..] {
                missing(writer, child)?;
            }
            result.children.truncate(retained);
        }
        -21 => {
            let (typ, wire_depth) = present(writer, typ, kind, depth.wire)?;
            let (id, child) = source.variant(value)?;
            let index = typ
                .labels
                .binary_search(&id)
                .map_err(|_| Error("missing derived variant".into()))?;
            writer.integer(index, false)?;
            set_unary(result, -21);
            result.labels.push(id);
            emit_into(
                source,
                child,
                &typ.children[index],
                Depth {
                    source: depth.source + 1,
                    wire: wire_depth + 1,
                },
                writer,
                budget,
                &mut result.children[0],
            )?;
        }
        op if scalar_kind(op) => {
            let value = source.scalar(value)?;
            validate_scalar(&value, op)?;
            tree::emit_tree(writer, &value, typ, depth.wire)?;
            set_scalar(result, op);
        }
        _ => return bad("unsupported source value kind"),
    }
    Ok(kind)
}

/// Derive used shapes, then emit from the original native values. Scalar
/// payloads are read only during emission; no ordinary encoding is parsed and
/// no payload tree is constructed. Both visits enforce the profile limits.
pub fn encode_source_args<S: DirectSource + ?Sized>(
    source: &S,
    values: &[S::Value],
) -> Result<Vec<u8>> {
    length(values.len())?;
    let mut budget = Budget::default();
    let types = values
        .iter()
        .map(|value| derive(source, *value, 0, &mut budget))
        .collect::<Result<Vec<_>>>()?;
    let mut writer = Writer(
        Vec::with_capacity(budget.bytes.saturating_add(128).min(MAX_BYTES)),
        false,
    );
    write_header(&mut writer, &types)?;
    let mut emitted = Budget::default();
    for (value, typ) in values.iter().zip(&types) {
        let mut actual = scalar(-17);
        emit_into(
            source,
            *value,
            typ,
            Depth { source: 0, wire: 0 },
            &mut writer,
            &mut emitted,
            &mut actual,
        )?;
        if actual != *typ {
            return bad("source shape changed between visits");
        }
    }
    Ok(writer.0)
}
