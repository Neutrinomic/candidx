//! `cx`: value-based Candid bytes, without hashing.
#![cfg_attr(not(feature = "std"), no_std)]
extern crate alloc;

use alloc::borrow::Cow;
use alloc::boxed::Box;
use alloc::collections::BTreeMap;
use alloc::string::String;
use alloc::{vec, vec::Vec};
use core::fmt;

mod tree;
pub use tree::{encode_direct_args, DirectField, DirectValue};
mod visitor;
pub use visitor::{encode_source_args, DirectSource, SourceField};
#[cfg(feature = "candid")]
mod typed;
#[cfg(feature = "candid")]
pub use typed::{
    encode_args, encode_args_with_types, encode_one, encode_one_with_type, ArgumentEncoder,
};

pub const PROFILE: &str = "cx";
const MAX_BYTES: usize = 32 * 1024 * 1024;
const MAX_ITEMS: usize = 2_000_000;
const MAX_TYPES: usize = 1024;
const MAX_DEPTH: usize = 256;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Error(pub String);
impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        self.0.fmt(f)
    }
}
impl core::error::Error for Error {}
type Result<T> = core::result::Result<T, Error>;
fn bad<T>(message: &str) -> Result<T> {
    Err(Error(message.into()))
}

#[derive(Clone, Debug)]
struct Node {
    op: i32,
    labels: Vec<u32>,
    edges: Vec<i32>,
}
#[derive(Clone, Debug)]
enum Value<'a> {
    Scalar(i32, Cow<'a, [u8]>),
    Some(Box<Value<'a>>),
    Vec(Vec<Value<'a>>),
    Blob(&'a [u8]),
    Text(&'a [u8]),
    Principal(&'a [u8]),
    Record(Vec<(u32, Value<'a>)>),
    Variant(u32, Box<Value<'a>>),
}
fn null<'a>() -> Value<'a> {
    Value::Scalar(-1, Cow::Borrowed(&[]))
}
fn is_null(v: &Value<'_>) -> bool {
    matches!(v, Value::Scalar(-1, _))
}
#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord)]
struct Shape {
    op: i32,
    labels: Vec<u32>,
    children: Vec<Shape>,
}
fn scalar(op: i32) -> Shape {
    Shape {
        op,
        labels: vec![],
        children: vec![],
    }
}
fn unary(op: i32, child: Shape) -> Shape {
    Shape {
        op,
        labels: vec![],
        children: vec![child],
    }
}

struct Reader<'a> {
    data: &'a [u8],
    pos: usize,
}
impl<'a> Reader<'a> {
    fn take(&mut self, n: usize) -> Result<&'a [u8]> {
        if n > self.data.len() - self.pos {
            return bad("truncated message");
        }
        let bytes = &self.data[self.pos..self.pos + n];
        self.pos += n;
        Ok(bytes)
    }
    fn byte(&mut self) -> Result<u8> {
        Ok(self.take(1)?[0])
    }
    // Only type references, labels, and lengths use this reader (at most ten
    // groups = 70 bits). Arbitrarily large Nat/Int values use scalar_integer.
    fn integer(&mut self, signed: bool, max: usize) -> Result<i128> {
        let mut value = 0i128;
        for i in 0..max {
            let byte = self.byte()?;
            value |= i128::from(byte & 127) << (7 * i);
            if byte < 128 {
                if signed && byte & 64 != 0 {
                    value -= 1i128 << (7 * (i + 1));
                }
                return Ok(value);
            }
        }
        bad("integer length limit")
    }
    // Normalize a scalar LEB directly. Constructing a BigInt only to encode it
    // again is unnecessary; leading zero/sign groups determine minimality.
    fn scalar_integer(&mut self, signed: bool) -> Result<Cow<'a, [u8]>> {
        let start = self.pos;
        for _ in 0..4096 {
            if self.byte()? < 128 {
                let mut end = self.pos;
                while end - start > 1 {
                    let last = self.data[end - 1] & 127;
                    let previous = self.data[end - 2];
                    if (!signed && last == 0)
                        || (signed
                            && ((last == 0 && previous & 64 == 0)
                                || (last == 127 && previous & 64 != 0)))
                    {
                        end -= 1;
                    } else {
                        break;
                    }
                }
                if end == self.pos {
                    return Ok(Cow::Borrowed(&self.data[start..end]));
                }
                let mut bytes = self.data[start..end].to_vec();
                if let Some(last) = bytes.last_mut() {
                    *last &= 127;
                }
                return Ok(Cow::Owned(bytes));
            }
        }
        bad("integer length limit")
    }
    fn length(&mut self) -> Result<usize> {
        let n =
            usize::try_from(self.integer(false, 10)?).map_err(|_| Error("index range".into()))?;
        if n > MAX_ITEMS {
            return bad("collection length limit");
        }
        Ok(n)
    }
    fn reference(&mut self) -> Result<i32> {
        i32::try_from(self.integer(true, 10)?).map_err(|_| Error("type index range".into()))
    }
    fn refs(&mut self) -> Result<Vec<i32>> {
        let n = self.length()?;
        (0..n).map(|_| self.reference()).collect()
    }
    fn text(&mut self) -> Result<&'a [u8]> {
        let n = self.length()?;
        let bytes = self.take(n)?;
        core::str::from_utf8(bytes).map_err(|_| Error("invalid UTF-8".into()))?;
        Ok(bytes)
    }
}

fn parse(r: &mut Reader<'_>) -> Result<(Vec<Node>, Vec<i32>)> {
    if r.take(4)? != b"DIDL" {
        return bad("missing DIDL magic");
    }
    let n = r.length()?;
    if n > MAX_TYPES {
        return bad("input type-table limit");
    }
    let mut nodes = Vec::new();
    for _ in 0..n {
        let op = r.reference()?;
        let mut labels = Vec::new();
        let mut edges = Vec::new();
        match op {
            -18 | -19 => edges.push(r.reference()?),
            -20 | -21 => {
                let count = r.length()?;
                for _ in 0..count {
                    let id = u32::try_from(r.integer(false, 10)?)
                        .map_err(|_| Error("field ID range".into()))?;
                    if labels.last().is_some_and(|last| *last >= id) {
                        return bad("unordered field IDs");
                    }
                    labels.push(id);
                    edges.push(r.reference()?);
                }
            }
            -22 => {
                let args = r.refs()?;
                let rets = r.refs()?;
                let count = r.length()?;
                let modes = r.take(count)?;
                if count > 1
                    || (count == 1 && !(1..=3).contains(&modes[0]))
                    || (modes.first() == Some(&2) && !rets.is_empty())
                {
                    return bad("invalid function annotations");
                }
                edges.extend(args);
                edges.extend(rets);
            }
            -23 => {
                let count = r.length()?;
                let mut previous: Option<Vec<u8>> = None;
                for _ in 0..count {
                    let name = r.text()?;
                    if previous.as_ref().is_some_and(|p| p.as_slice() >= name) {
                        return bad("unordered service methods");
                    }
                    previous = Some(name.to_vec());
                    edges.push(r.reference()?);
                }
            }
            _ => return bad("unsupported compound type"),
        }
        nodes.push(Node { op, labels, edges });
    }
    let roots = r.refs()?;
    for &reference in roots.iter().chain(nodes.iter().flat_map(|n| &n.edges)) {
        if (reference >= 0 && reference as usize >= nodes.len())
            || (reference < 0 && !(-17..=-1).contains(&reference) && reference != -24)
        {
            return bad("invalid type reference");
        }
    }
    for node in &nodes {
        if node.op == -23
            && node
                .edges
                .iter()
                .any(|e| *e < 0 || nodes[*e as usize].op != -22)
        {
            return bad("method must be func");
        }
    }
    Ok((nodes, roots))
}

fn read_value<'a>(
    r: &mut Reader<'a>,
    nodes: &[Node],
    reference: i32,
    depth: usize,
    steps: &mut usize,
) -> Result<Value<'a>> {
    *steps += 1;
    if *steps > MAX_ITEMS || depth > MAX_DEPTH {
        return bad("value traversal limit");
    }
    if reference >= 0 {
        let node = &nodes[reference as usize];
        return match node.op {
            -18 => match r.byte()? {
                0 => Ok(null()),
                1 => Ok(Value::Some(Box::new(read_value(
                    r,
                    nodes,
                    node.edges[0],
                    depth + 1,
                    steps,
                )?))),
                _ => bad("invalid option tag"),
            },
            -19 => {
                let n = r.length()?;
                if node.edges[0] == -5 {
                    *steps += n;
                    if *steps > MAX_ITEMS || (n != 0 && depth + 1 > MAX_DEPTH) {
                        return bad("value traversal limit");
                    }
                    return Ok(Value::Blob(r.take(n)?));
                }
                // Exact reservation for ordinary workloads, bounded eager
                // allocation for truncated or very large input declarations.
                let mut items = Vec::with_capacity(n.min(4096));
                for _ in 0..n {
                    items.push(read_value(r, nodes, node.edges[0], depth + 1, steps)?);
                }
                Ok(Value::Vec(items))
            }
            -20 => {
                let mut fields = Vec::new();
                for (&label, &edge) in node.labels.iter().zip(&node.edges) {
                    let child = read_value(r, nodes, edge, depth + 1, steps)?;
                    let absent = edge >= 0 && nodes[edge as usize].op == -18 && is_null(&child);
                    if !absent {
                        // The validated type table already sorts record labels.
                        fields.push((label, child));
                    }
                }
                Ok(Value::Record(fields))
            }
            -21 => {
                let index = r.length()?;
                if index >= node.edges.len() {
                    return bad("invalid variant index");
                }
                Ok(Value::Variant(
                    node.labels[index],
                    Box::new(read_value(r, nodes, node.edges[index], depth + 1, steps)?),
                ))
            }
            _ => bad("function/service values outside data profile"),
        };
    }
    let raw = match reference {
        -1 => return Ok(null()),
        -16 => Cow::Borrowed(&[][..]),
        -17 => return bad("empty has no values"),
        -2 => {
            let bytes = r.take(1)?;
            if bytes[0] > 1 {
                return bad("invalid bool");
            }
            Cow::Borrowed(bytes)
        }
        -3 | -4 => r.scalar_integer(reference == -4)?,
        -12..=-5 => Cow::Borrowed(r.take(1 << ((-reference - 5) % 4))?),
        -13 | -14 => {
            let size = if reference == -13 { 4 } else { 8 };
            let bytes = r.take(size)?;
            let mut bits = 0u64;
            for (i, byte) in bytes.iter().enumerate() {
                bits |= u64::from(*byte) << (8 * i);
            }
            let (frac, exponent) = if size == 4 { (23, 8) } else { (52, 11) };
            if (bits >> frac) & ((1 << exponent) - 1) == (1 << exponent) - 1
                && bits & ((1u64 << frac) - 1) != 0
            {
                let canonical: u64 = if size == 4 {
                    0x7fc00000
                } else {
                    0x7ff8000000000000
                };
                Cow::Owned(canonical.to_le_bytes()[..size].to_vec())
            } else {
                Cow::Borrowed(bytes)
            }
        }
        -15 => return Ok(Value::Text(r.text()?)),
        -24 => {
            if r.byte()? != 1 {
                return bad("opaque principal");
            }
            let n = r.length()?;
            if n > 29 {
                return bad("principal length exceeds 29");
            }
            return Ok(Value::Principal(r.take(n)?));
        }
        _ => return bad("unsupported primitive"),
    };
    Ok(Value::Scalar(reference, raw))
}

// Consume each newly derived shape and extend the accumulated array shape.
// Equal shapes need no clone; sorted label vectors permit a linear union.
fn join(a: &mut Shape, mut b: Shape, depth: usize) -> Result<()> {
    if depth > MAX_DEPTH {
        return bad("type nesting limit");
    }
    if *a == b || b.op == -17 {
        return Ok(());
    }
    if a.op == -17 {
        *a = b;
        return Ok(());
    }
    if a.op == -1 {
        *a = if b.op == -18 { b } else { unary(-18, b) };
        return Ok(());
    }
    if b.op == -1 {
        if a.op != -18 {
            let child = core::mem::replace(a, scalar(-17));
            *a = unary(-18, child);
        }
        return Ok(());
    }
    if a.op == -18 || b.op == -18 {
        if b.op == -18 {
            b = b
                .children
                .pop()
                .ok_or_else(|| Error("missing option shape".into()))?;
        }
        if a.op != -18 {
            let child = core::mem::replace(a, scalar(-17));
            *a = unary(-18, child);
        }
        return join(&mut a.children[0], b, depth + 1);
    }
    if a.op != b.op || ![-19, -20, -21].contains(&a.op) {
        return bad("incompatible value shapes");
    }
    if a.op == -19 {
        let child = b
            .children
            .pop()
            .ok_or_else(|| Error("missing vector shape".into()))?;
        return join(&mut a.children[0], child, depth + 1);
    }
    if a.labels == b.labels {
        for (left, right) in a.children.iter_mut().zip(b.children) {
            join(left, right, depth + 1)?;
        }
        return Ok(());
    }
    let capacity = a.labels.len().max(b.labels.len());
    let mut left = core::mem::take(&mut a.labels)
        .into_iter()
        .zip(core::mem::take(&mut a.children))
        .peekable();
    let mut right = b.labels.into_iter().zip(b.children).peekable();
    let mut labels = Vec::with_capacity(capacity);
    let mut children = Vec::with_capacity(capacity);
    while left.peek().is_some() || right.peek().is_some() {
        let take_left = match (left.peek(), right.peek()) {
            (Some((x, _)), Some((y, _))) => x <= y,
            (Some(_), None) => true,
            _ => false,
        };
        let (id, mut child) = if take_left {
            left.next()
                .ok_or_else(|| Error("missing left shape".into()))?
        } else {
            let (id, child) = right
                .next()
                .ok_or_else(|| Error("missing right shape".into()))?;
            let child = if a.op == -21 {
                child
            } else {
                let mut absent = scalar(-1);
                join(&mut absent, child, depth + 1)?;
                absent
            };
            labels.push(id);
            children.push(child);
            continue;
        };
        if right.peek().is_some_and(|(label, _)| *label == id) {
            let (_, other) = right
                .next()
                .ok_or_else(|| Error("missing joined shape".into()))?;
            join(&mut child, other, depth + 1)?;
        } else if a.op == -20 {
            join(&mut child, scalar(-1), depth + 1)?;
        }
        labels.push(id);
        children.push(child);
    }
    a.labels = labels;
    a.children = children;
    Ok(())
}
// This predicate is exactly the no-widening case of J(t, N(value)): a present
// option consumes one option layer, an absent field can keep null/opt, and a
// variant must already contain its selected case. It avoids constructing and
// immediately discarding a new shape for each homogeneous array element.
fn fits_shape(value: &Value<'_>, typ: &Shape, depth: usize) -> bool {
    if depth > MAX_DEPTH {
        return false;
    }
    if typ.op == -18 {
        return is_null(value)
            || fits_shape(
                if let Value::Some(child) = value {
                    child
                } else {
                    value
                },
                &typ.children[0],
                depth + 1,
            );
    }
    match value {
        Value::Scalar(op, _) => *op == typ.op,
        Value::Text(_) => typ.op == -15,
        Value::Principal(_) => typ.op == -24,
        Value::Blob(_) => typ.op == -19 && typ.children[0].op == -5,
        Value::Vec(items) if typ.op == -19 => items
            .iter()
            .all(|child| fits_shape(child, &typ.children[0], depth + 1)),
        Value::Record(fields) if typ.op == -20 => {
            let mut next = fields.iter().peekable();
            for (id, child_typ) in typ.labels.iter().zip(&typ.children) {
                if next.peek().is_some_and(|(label, _)| label == id) {
                    let Some((_, child)) = next.next() else {
                        return false;
                    };
                    if !fits_shape(child, child_typ, depth + 1) {
                        return false;
                    }
                } else if ![-1, -18].contains(&child_typ.op) {
                    return false;
                }
            }
            next.next().is_none()
        }
        Value::Variant(id, child) if typ.op == -21 => typ
            .labels
            .binary_search(id)
            .is_ok_and(|index| fits_shape(child, &typ.children[index], depth + 1)),
        _ => false,
    }
}

fn shape(value: &Value<'_>, depth: usize) -> Result<Shape> {
    if depth > MAX_DEPTH {
        return bad("type nesting limit");
    }
    match value {
        Value::Scalar(op, _) => Ok(scalar(*op)),
        Value::Some(child) => Ok(unary(-18, shape(child, depth + 1)?)),
        Value::Blob(_) => Ok(unary(-19, scalar(-5))),
        Value::Text(_) => Ok(scalar(-15)),
        Value::Principal(_) => Ok(scalar(-24)),
        Value::Vec(items) => {
            let mut element = scalar(-17);
            for child in items {
                if !fits_shape(child, &element, depth + 1) {
                    join(&mut element, shape(child, depth + 1)?, 0)?;
                }
            }
            Ok(unary(-19, element))
        }
        Value::Record(fields) => Ok(Shape {
            op: -20,
            labels: fields.iter().map(|(id, _)| *id).collect(),
            children: fields
                .iter()
                .map(|(_, v)| shape(v, depth + 1))
                .collect::<Result<_>>()?,
        }),
        Value::Variant(id, child) => Ok(Shape {
            op: -21,
            labels: vec![*id],
            children: vec![shape(child, depth + 1)?],
        }),
    }
}
struct Writer(Vec<u8>, bool);
impl Writer {
    fn add(&mut self, bytes: &[u8]) -> Result<()> {
        if bytes.len() > MAX_BYTES - self.0.len() {
            self.1 = true;
            return bad("output byte limit");
        }
        self.0.extend(bytes);
        Ok(())
    }
    fn integer(&mut self, n: impl TryInto<i64>, signed: bool) -> Result<()> {
        let mut value = n.try_into().map_err(|_| Error("index range".into()))?;
        // Wire metadata is bounded to u32 labels, 32 MiB payload lengths, and
        // 1024 type-table entries. A stack buffer avoids a heap Vec per index.
        let mut bytes = [0u8; 10];
        let mut length = 0;
        loop {
            let byte = (value & 127) as u8;
            value >>= 7;
            let done = if signed {
                (value == 0 && byte < 64) || (value == -1 && byte >= 64)
            } else {
                value == 0
            };
            bytes[length] = byte | if done { 0 } else { 128 };
            length += 1;
            if done {
                return self.add(&bytes[..length]);
            }
        }
    }
}
fn intern<'a>(
    t: &'a Shape,
    table: &mut Vec<&'a Shape>,
    indices: &mut BTreeMap<&'a Shape, i32>,
) -> Result<i32> {
    if ![-18, -19, -20, -21].contains(&t.op) {
        return Ok(t.op);
    }
    if let Some(id) = indices.get(t) {
        return Ok(*id);
    }
    if table.len() >= MAX_TYPES {
        return bad("output type-table limit");
    }
    let id = table.len() as i32;
    indices.insert(t, id);
    table.push(t);
    for child in &t.children {
        intern(child, table, indices)?;
    }
    Ok(id)
}

fn write_header(w: &mut Writer, types: &[Shape]) -> Result<()> {
    if types.len() > MAX_ITEMS {
        return bad("collection length limit");
    }
    let mut table = Vec::new();
    let mut indices = BTreeMap::new();
    let roots = types
        .iter()
        .map(|t| intern(t, &mut table, &mut indices))
        .collect::<Result<Vec<_>>>()?;
    w.add(b"DIDL")?;
    w.integer(table.len(), false)?;
    for t in &table {
        w.integer(t.op, true)?;
        if t.op == -18 || t.op == -19 {
            let child = &t.children[0];
            w.integer(
                if [-18, -19, -20, -21].contains(&child.op) {
                    indices[child]
                } else {
                    child.op
                },
                true,
            )?;
        } else {
            w.integer(t.labels.len(), false)?;
            for (id, child) in t.labels.iter().zip(&t.children) {
                w.integer(*id, false)?;
                w.integer(
                    if [-18, -19, -20, -21].contains(&child.op) {
                        indices[child]
                    } else {
                        child.op
                    },
                    true,
                )?;
            }
        }
    }
    w.integer(roots.len(), false)?;
    for root in roots {
        w.integer(root, true)?;
    }
    Ok(())
}
fn emit(w: &mut Writer, v: &Value<'_>, t: &Shape, depth: usize) -> Result<()> {
    if depth > MAX_DEPTH {
        return bad("emitted value nesting limit");
    }
    if t.op == -18 {
        if is_null(v) {
            return w.add(&[0]);
        }
        w.add(&[1])?;
        return emit(
            w,
            if let Value::Some(child) = v { child } else { v },
            &t.children[0],
            depth + 1,
        );
    }
    match v {
        Value::Scalar(op, raw) if *op == t.op => w.add(raw),
        Value::Text(bytes) if t.op == -15 => {
            w.integer(bytes.len(), false)?;
            w.add(bytes)
        }
        Value::Principal(bytes) if t.op == -24 => {
            w.add(&[1, bytes.len() as u8])?;
            w.add(bytes)
        }
        Value::Blob(bytes) if t.op == -19 && t.children[0].op == -5 => {
            w.integer(bytes.len(), false)?;
            w.add(bytes)
        }
        Value::Vec(items) if t.op == -19 => {
            w.integer(items.len(), false)?;
            for child in items {
                emit(w, child, &t.children[0], depth + 1)?;
            }
            Ok(())
        }
        Value::Record(fields) if t.op == -20 => {
            let absent = null();
            let mut next = fields.iter().peekable();
            for (id, typ) in t.labels.iter().zip(&t.children) {
                let child = if next.peek().is_some_and(|(label, _)| label == id) {
                    &next
                        .next()
                        .ok_or_else(|| Error("missing record field".into()))?
                        .1
                } else {
                    &absent
                };
                emit(w, child, typ, depth + 1)?;
            }
            Ok(())
        }
        Value::Variant(id, child) if t.op == -21 => {
            let index = t
                .labels
                .binary_search(id)
                .map_err(|_| Error("missing derived variant".into()))?;
            w.integer(index, false)?;
            emit(w, child, &t.children[index], depth + 1)
        }
        _ => bad("cannot emit at derived type"),
    }
}

/// Normalize the complete received message before any typed projection.
pub fn canonicalize(bytes: &[u8]) -> Result<Vec<u8>> {
    if bytes.len() > MAX_BYTES {
        return bad("input byte limit");
    }
    let mut r = Reader {
        data: bytes,
        pos: 0,
    };
    let (nodes, roots) = parse(&mut r)?;
    let payload_size = bytes.len() - r.pos;
    let mut steps = 0;
    let values = roots
        .iter()
        .map(|r0| read_value(&mut r, &nodes, *r0, 0, &mut steps))
        .collect::<Result<Vec<_>>>()?;
    if r.pos != bytes.len() {
        return bad("trailing payload");
    }
    let types = values
        .iter()
        .map(|v| shape(v, 0))
        .collect::<Result<Vec<_>>>()?;
    // Reserve the existing payload and a small header, avoiding a second full
    // payload allocation when a large Blob/Text is followed by small fields.
    // Ignored type declarations do not influence this reservation.
    let mut w = Writer(
        Vec::with_capacity(payload_size.saturating_add(128).min(MAX_BYTES)),
        false,
    );
    write_header(&mut w, &types)?;
    for (v, t) in values.iter().zip(&types) {
        emit(&mut w, v, t, 0)?;
    }
    Ok(w.0)
}

#[cfg(test)]
mod tests {
    use super::*;
    use alloc::collections::BTreeSet;

    #[test]
    fn no_widening_fast_path_agrees_with_join_for_nested_values() {
        let number = Value::Scalar(-3, Cow::Borrowed(&[1]));
        let seeds = vec![
            null(),
            number.clone(),
            Value::Scalar(-5, Cow::Borrowed(&[1])),
            Value::Some(Box::new(null())),
            Value::Some(Box::new(number)),
            Value::Some(Box::new(Value::Some(Box::new(null())))),
            Value::Blob(&[]),
            Value::Text(&[]),
            Value::Vec(vec![]),
            Value::Record(vec![]),
            Value::Record(vec![(0, null())]),
            Value::Record(vec![(1, Value::Some(Box::new(null())))]),
            Value::Variant(0, Box::new(null())),
            Value::Variant(1, Box::new(Value::Some(Box::new(null())))),
        ];
        let mut values = seeds.clone();
        for a in &seeds {
            values.push(Value::Some(Box::new(a.clone())));
            for b in &seeds {
                values.push(Value::Vec(vec![a.clone(), b.clone()]));
                values.push(Value::Record(vec![(0, a.clone()), (1, b.clone())]));
            }
        }
        let mut types: BTreeSet<_> = values.iter().filter_map(|v| shape(v, 0).ok()).collect();
        let seed_types: Vec<_> = seeds.iter().map(|v| shape(v, 0).unwrap()).collect();
        for a in &seed_types {
            for b in &seed_types {
                let mut joined = a.clone();
                if join(&mut joined, b.clone(), 0).is_ok() {
                    types.insert(joined);
                }
            }
        }
        for typ in types {
            for value in &values {
                if fits_shape(value, &typ, 0) {
                    let mut joined = typ.clone();
                    join(&mut joined, shape(value, 0).unwrap(), 0).unwrap();
                    assert_eq!(joined, typ, "value {value:?}");
                }
            }
        }
    }

    #[test]
    fn metadata_indices_keep_full_width_and_reject_overflow() {
        let max_label = b"DIDL\x01\x6c\x01\xff\xff\xff\xff\x0f\x7b\x01\0\xff";
        assert_eq!(canonicalize(max_label).unwrap(), max_label);
        let overflowing_label = b"DIDL\x01\x6c\x01\x80\x80\x80\x80\x10\x7b\x01\0\xff";
        assert_eq!(
            canonicalize(overflowing_label).unwrap_err().0,
            "field ID range"
        );
        let overlong_reference = b"DIDL\0\x01\xfe\xff\xff\xff\xff\xff\xff\xff\xff\x7f\0";
        assert_eq!(
            canonicalize(overlong_reference).unwrap(),
            b"DIDL\0\x01\x7e\0"
        );
        let overflowing_reference = b"DIDL\0\x01\x80\x80\x80\x80\x08";
        assert_eq!(
            canonicalize(overflowing_reference).unwrap_err().0,
            "type index range"
        );
    }
}
