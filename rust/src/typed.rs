//! Two visits of CandidType's value callbacks, without an ordinary message.
use super::*;
use candid::types::{CandidType, Compound as CandidCompound, Serializer, Type, TypeEnv, TypeInner};
use std::io;

use crate::visitor::{join_scratch, set_scalar};

fn set_unary(shape: &mut Shape, op: i32) {
    // A selected variant has one label; the shared helper reserves its child.
    if op == -21 && shape.labels.is_empty() {
        shape.labels.reserve_exact(1);
    }
    crate::visitor::set_unary(shape, op);
}

type CResult<T> = core::result::Result<T, candid::Error>;
#[cold]
fn error(message: impl fmt::Display) -> candid::Error {
    candid::Error::msg(message.to_string())
}
#[inline]
fn resolve(env: &TypeEnv, source: &Type) -> CResult<Type> {
    let mut current = source.clone();
    for _ in 0..=MAX_DEPTH {
        current = match current.as_ref() {
            TypeInner::Var(name) => env.find_type(name).map_err(error)?.clone(),
            TypeInner::Knot(id) => candid::types::internal::find_type(id)
                .ok_or_else(|| error("unbound recursive Candid type"))?,
            _ => return Ok(current),
        };
    }
    Err(error("source type alias nesting limit"))
}
fn opcode(source: &Type) -> Option<i32> {
    use TypeInner::*;
    Some(match source.as_ref() {
        Null => -1,
        Bool => -2,
        Nat => -3,
        Int => -4,
        Nat8 => -5,
        Nat16 => -6,
        Nat32 => -7,
        Nat64 => -8,
        Int8 => -9,
        Int16 => -10,
        Int32 => -11,
        Int64 => -12,
        Float32 => -13,
        Float64 => -14,
        Text => -15,
        Reserved => -16,
        Empty => -17,
        Opt(_) => -18,
        Vec(_) => -19,
        Record(_) => -20,
        Variant(_) => -21,
        Principal => -24,
        _ => return None,
    })
}
fn length(n: usize) -> CResult<()> {
    if n > MAX_ITEMS {
        Err(error("collection length limit"))
    } else {
        Ok(())
    }
}
fn fields_order(fields: &[candid::types::Field]) -> CResult<()> {
    length(fields.len())?;
    if fields
        .windows(2)
        .any(|pair| pair[0].id.get_id() >= pair[1].id.get_id())
    {
        return Err(error("unordered field IDs"));
    }
    Ok(())
}

// Source metadata is cached only up to a fixed small size. Larger valid
// schemas use the original streaming checks instead of growing the cache.
const SOURCE_CACHE_NODES: usize = MAX_TYPES;
const SOURCE_CACHE_FIELDS: usize = MAX_TYPES * 4;
#[derive(Clone)]
enum Source {
    Cached(usize),
    Uncached(Type),
}
#[derive(Clone)]
struct SourceField {
    id: u32,
    source: Source,
}
struct SourceNode {
    // Retaining this Rc prevents pointer-key reuse across both value visits.
    original: Type,
    alias: Option<Source>,
    op: Option<i32>,
    child: Option<Source>,
    checked: bool,
    fields: Option<Vec<SourceField>>,
    cases: std::collections::BTreeMap<usize, SourceField>,
}
struct Sources<'a> {
    env: &'a TypeEnv,
    indices: std::collections::BTreeMap<usize, usize>,
    nodes: Vec<SourceNode>,
    fields_cached: usize,
    enabled: bool,
}
impl<'a> Sources<'a> {
    fn new(env: &'a TypeEnv) -> Self {
        Self {
            env,
            indices: Default::default(),
            nodes: Vec::new(),
            fields_cached: 0,
            enabled: false,
        }
    }
    fn add(&mut self, source: &Type) -> Source {
        if !self.enabled {
            return Source::Uncached(source.clone());
        }
        let key = std::rc::Rc::as_ptr(&source.0) as usize;
        if let Some(index) = self.indices.get(&key) {
            return Source::Cached(*index);
        }
        if self.nodes.len() == SOURCE_CACHE_NODES {
            return Source::Uncached(source.clone());
        }
        let index = self.nodes.len();
        self.nodes.push(SourceNode {
            original: source.clone(),
            alias: None,
            op: opcode(source),
            child: None,
            checked: false,
            fields: None,
            cases: Default::default(),
        });
        self.indices.insert(key, index);
        Source::Cached(index)
    }
    fn add_owned(&mut self, source: Type) -> Source {
        if !self.enabled || self.nodes.len() == SOURCE_CACHE_NODES {
            return Source::Uncached(source);
        }
        self.add(&source)
    }
    #[inline]
    fn original<'b>(&'b self, source: &'b Source) -> &'b Type {
        match source {
            Source::Cached(index) => &self.nodes[*index].original,
            Source::Uncached(original) => original,
        }
    }
    #[inline]
    fn resolve(&mut self, source: Source) -> CResult<Source> {
        let original = self.original(&source);
        if !matches!(original.as_ref(), TypeInner::Var(_) | TypeInner::Knot(_)) {
            return Ok(match source {
                Source::Uncached(original) if self.enabled => self.add_owned(original),
                source => source,
            });
        }
        if let Source::Cached(index) = &source {
            if let Some(alias) = &self.nodes[*index].alias {
                return Ok(alias.clone());
            }
        }
        let resolved = resolve(self.env, original)?;
        let target = self.add_owned(resolved);
        if let Source::Cached(index) = source {
            self.nodes[index].alias = Some(target.clone());
        }
        Ok(target)
    }
    #[inline]
    fn op(&self, source: &Source) -> Option<i32> {
        match source {
            Source::Cached(index) => self.nodes[*index].op,
            Source::Uncached(original) => opcode(original),
        }
    }
    fn child(&mut self, source: &Source) -> CResult<Source> {
        if let Source::Cached(index) = source {
            if let Some(child) = self.nodes[*index].child.clone() {
                return self.resolve(child);
            }
        }
        let child = match self.original(source).as_ref() {
            TypeInner::Opt(child) | TypeInner::Vec(child) => child.clone(),
            _ => return Err(error("missing source child type")),
        };
        let child = self.add_owned(child);
        if let Source::Cached(index) = source {
            self.nodes[*index].child = Some(child.clone());
        }
        self.resolve(child)
    }
    fn prepare_fields(&mut self, source: &Source) -> CResult<()> {
        if let Source::Cached(index) = source {
            if self.nodes[*index].checked {
                return Ok(());
            }
        }
        let original = self.original(source).clone();
        let (fields, record) = match original.as_ref() {
            TypeInner::Record(fields) => (fields, true),
            TypeInner::Variant(fields) => (fields, false),
            _ => return Err(error("record or variant source metadata missing")),
        };
        fields_order(fields)?;
        if let Source::Cached(index) = source {
            // Variant child descriptors are registered only when selected.
            if record && fields.len() <= SOURCE_CACHE_FIELDS - self.fields_cached {
                let mut cached = Vec::with_capacity(fields.len());
                for field in fields {
                    cached.push(SourceField {
                        id: field.id.get_id(),
                        source: self.add(&field.ty),
                    });
                }
                self.fields_cached += fields.len();
                self.nodes[*index].fields = Some(cached);
            }
            self.nodes[*index].checked = true;
        }
        Ok(())
    }
    #[inline]
    fn field(&mut self, source: &Source, field: usize) -> Option<SourceField> {
        if let Source::Cached(index) = source {
            let node = &self.nodes[*index];
            if let Some(fields) = &node.fields {
                return fields.get(field).cloned();
            }
            if let Some(case) = node.cases.get(&field) {
                return Some(case.clone());
            }
        }
        let fields = match self.original(source).as_ref() {
            TypeInner::Record(fields) | TypeInner::Variant(fields) => fields,
            _ => return None,
        };
        let original_field = fields.get(field)?;
        let id = original_field.id.get_id();
        let child = original_field.ty.clone();
        let cached = SourceField {
            id,
            source: self.add_owned(child),
        };
        if let Source::Cached(index) = source {
            if self.op(source) == Some(-21) && self.fields_cached < SOURCE_CACHE_FIELDS {
                self.nodes[*index].cases.insert(field, cached.clone());
                self.fields_cached += 1;
            }
        }
        Some(cached)
    }
    #[inline]
    fn fields_len(&self, source: &Source) -> usize {
        match self.original(source).as_ref() {
            TypeInner::Record(fields) | TypeInner::Variant(fields) => fields.len(),
            _ => 0,
        }
    }
}

#[derive(Default)]
struct Budget {
    steps: usize,
    bytes: usize,
    events: usize,
    compounds: usize,
    failure: Option<String>,
}
impl Budget {
    fn visit(&mut self, depth: usize) -> CResult<()> {
        self.charge(1, depth)
    }
    fn charge(&mut self, n: usize, depth: usize) -> CResult<()> {
        self.steps = self.steps.saturating_add(n);
        if self.steps > MAX_ITEMS || depth > MAX_DEPTH {
            return Err(error("value traversal limit"));
        }
        Ok(())
    }
    fn size(&mut self, n: usize) {
        self.bytes = self.bytes.saturating_add(n).min(MAX_BYTES);
    }
    #[inline]
    fn check(&mut self) -> CResult<()> {
        match self.failure.take() {
            Some(message) => Err(error(message)),
            None => Ok(()),
        }
    }
    fn fail(&mut self, message: impl fmt::Display) {
        if self.failure.is_none() {
            self.failure = Some(message.to_string());
        }
    }
}
enum Phase<'a> {
    Derive(&'a mut Shape),
    Emit {
        typ: &'a Shape,
        writer: &'a mut Writer,
        actual: &'a mut Shape,
    },
}
impl Phase<'_> {
    fn borrow(&mut self) -> Phase<'_> {
        match self {
            Self::Derive(shape) => Phase::Derive(shape),
            Self::Emit {
                typ,
                writer,
                actual,
            } => Phase::Emit {
                typ,
                writer,
                actual,
            },
        }
    }
}
struct Pass<'a, 'env> {
    source: Source,
    sources: &'a mut Sources<'env>,
    budget: &'a mut Budget,
    phase: Phase<'a>,
    depth: usize,
}
#[inline(always)]
fn visit<T: CandidType + ?Sized>(
    value: &T,
    source: Source,
    sources: &mut Sources<'_>,
    budget: &mut Budget,
    mut phase: Phase<'_>,
    depth: usize,
    count: bool,
) -> CResult<()> {
    let result = (|| {
        // A reused shape must not let a swallowed callback error appear as a
        // complete value from the preceding vector element. Keep its buffers.
        match &mut phase {
            Phase::Derive(shape) => shape.op = -17,
            Phase::Emit { actual, .. } => actual.op = -17,
        }
        if count {
            budget.visit(depth)?;
        }
        // Bulk primitive vectors charge their items without individual value
        // callbacks. Cache setup is worthwhile only after repeated callbacks.
        if budget.events >= 32 {
            sources.enabled = true;
        }
        let source = sources.resolve(source)?;
        let events = budget.events;
        let compounds = budget.compounds;
        value.idl_serialize(Pass {
            source,
            sources,
            budget,
            phase: phase.borrow(),
            depth,
        })?;
        budget.check()?;
        if budget.compounds != compounds {
            return Err(error("Candid serializer left a compound unfinished"));
        }
        if budget.events == events {
            return Err(error("Candid serializer emitted no value callback"));
        }
        let shape = match &phase {
            Phase::Derive(shape) => shape,
            Phase::Emit { actual, .. } => actual,
        };
        if shape.op == -17 {
            return Err(error("Candid serializer emitted no complete value"));
        }
        if let Phase::Emit { writer, .. } = &phase {
            if writer.1 {
                return Err(error("output byte limit"));
            }
        }
        Ok(())
    })();
    if let Err(ref error) = result {
        budget.fail(error);
    }
    result
}
fn present<'a>(
    mut typ: &'a Shape,
    writer: &mut Writer,
    op: i32,
    mut depth: usize,
) -> CResult<&'a Shape> {
    while typ.op == -18 {
        if op == -1 {
            writer.add(&[0]).map_err(error)?;
            return Ok(typ);
        }
        writer.add(&[1]).map_err(error)?;
        typ = &typ.children[0];
        depth += 1;
        if depth > MAX_DEPTH {
            return Err(error("emitted value nesting limit"));
        }
    }
    if typ.op != op {
        return Err(error(
            "serializer value changed between visits or disagrees with source type",
        ));
    }
    Ok(typ)
}
impl Pass<'_, '_> {
    #[inline(always)]
    fn scalar(
        mut self,
        op: i32,
        size: usize,
        write: impl FnOnce(&mut Writer) -> CResult<()>,
    ) -> CResult<()> {
        self.budget.events += 1;
        if self.sources.op(&self.source) != Some(op) {
            return Err(error("scalar callback disagrees with source type"));
        }
        match &mut self.phase {
            Phase::Derive(shape) => {
                set_scalar(shape, op);
                self.budget.size(size);
                Ok(())
            }
            Phase::Emit {
                typ,
                writer,
                actual,
            } => {
                set_scalar(actual, op);
                present(typ, writer, op, self.depth)?;
                if op == -1 {
                    Ok(())
                } else {
                    write(writer)
                }
            }
        }
    }
}
struct ScalarWriter<'a> {
    writer: &'a mut Writer,
    bytes: usize,
}
impl io::Write for ScalarWriter<'_> {
    fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
        if self.bytes.saturating_add(bytes.len()) > 4096 {
            return Err(io::Error::other("integer length limit"));
        }
        self.writer.add(bytes).map_err(io::Error::other)?;
        self.bytes += bytes.len();
        Ok(bytes.len())
    }
    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}
fn big_int_len(value: &candid::Int) -> CResult<usize> {
    let mut bits = value.0.bits() as usize;
    if bits > 4096 * 7 + 1 {
        return Err(error("integer length limit"));
    }
    if value.0 < Default::default()
        && value
            .0
            .iter_u64_digits()
            .map(|x| x.count_ones())
            .sum::<u32>()
            == 1
    {
        bits -= 1;
    }
    let n = (bits + 1).div_ceil(7).max(1);
    if n > 4096 {
        Err(error("integer length limit"))
    } else {
        Ok(n)
    }
}
macro_rules! fixed {
    ($method:ident, $ty:ty, $op:expr) => {
        fn $method(self, value: $ty) -> CResult<()> {
            self.scalar($op, core::mem::size_of::<$ty>(), |w| {
                w.add(&value.to_le_bytes()).map_err(error)
            })
        }
    };
}
impl<'a, 'env> Serializer for Pass<'a, 'env> {
    type Error = candid::Error;
    type Compound = Compound<'a, 'env>;
    #[inline]
    fn serialize_bool(self, value: bool) -> CResult<()> {
        self.scalar(-2, 1, |w| w.add(&[u8::from(value)]).map_err(error))
    }
    #[inline]
    fn serialize_nat(self, value: &candid::Nat) -> CResult<()> {
        let n = (value.0.bits() as usize).div_ceil(7).max(1);
        if n > 4096 {
            return Err(error("integer length limit"));
        }
        self.scalar(-3, n, |w| {
            value.encode(&mut ScalarWriter {
                writer: w,
                bytes: 0,
            })
        })
    }
    #[inline]
    fn serialize_int(self, value: &candid::Int) -> CResult<()> {
        self.scalar(-4, big_int_len(value)?, |w| {
            value.encode(&mut ScalarWriter {
                writer: w,
                bytes: 0,
            })
        })
    }
    #[inline]
    fn serialize_u128(self, value: u128) -> CResult<()> {
        let bytes = value.to_le_bytes();
        let n = tree::magnitude_leb_len(&bytes, false, false).map_err(error)?;
        self.scalar(-3, n, |w| {
            tree::emit_magnitude(w, &bytes, false, false).map_err(error)
        })
    }
    #[inline]
    fn serialize_i128(self, value: i128) -> CResult<()> {
        let bytes = value.unsigned_abs().to_le_bytes();
        let n = tree::magnitude_leb_len(&bytes, true, value < 0).map_err(error)?;
        self.scalar(-4, n, |w| {
            tree::emit_magnitude(w, &bytes, true, value < 0).map_err(error)
        })
    }
    fixed!(serialize_nat8, u8, -5);
    fixed!(serialize_nat16, u16, -6);
    fixed!(serialize_nat32, u32, -7);
    fixed!(serialize_nat64, u64, -8);
    fixed!(serialize_int8, i8, -9);
    fixed!(serialize_int16, i16, -10);
    fixed!(serialize_int32, i32, -11);
    fixed!(serialize_int64, i64, -12);
    #[inline]
    fn serialize_float32(self, value: f32) -> CResult<()> {
        self.scalar(-13, 4, |w| {
            w.add(
                &if value.is_nan() {
                    0x7fc0_0000u32
                } else {
                    value.to_bits()
                }
                .to_le_bytes(),
            )
            .map_err(error)
        })
    }
    #[inline]
    fn serialize_float64(self, value: f64) -> CResult<()> {
        self.scalar(-14, 8, |w| {
            w.add(
                &if value.is_nan() {
                    0x7ff8_0000_0000_0000u64
                } else {
                    value.to_bits()
                }
                .to_le_bytes(),
            )
            .map_err(error)
        })
    }
    #[inline]
    fn serialize_text(self, value: &str) -> CResult<()> {
        length(value.len())?;
        self.scalar(-15, value.len().saturating_add(10), |w| {
            w.integer(value.len(), false).map_err(error)?;
            w.add(value.as_bytes()).map_err(error)
        })
    }
    #[inline]
    fn serialize_null(self, _: ()) -> CResult<()> {
        let op = if self.sources.op(&self.source) == Some(-16) {
            -16
        } else {
            -1
        };
        self.scalar(op, 0, |_| Ok(()))
    }
    fn serialize_empty(self) -> CResult<()> {
        Err(error("empty has no values"))
    }
    #[inline]
    fn serialize_principal(self, value: &[u8]) -> CResult<()> {
        if value.len() > 29 {
            return Err(error("principal length exceeds 29"));
        }
        self.scalar(-24, value.len() + 2, |w| {
            w.add(&[1, value.len() as u8]).map_err(error)?;
            w.add(value).map_err(error)
        })
    }
    fn serialize_function(self, _: &[u8], _: &str) -> CResult<()> {
        Err(error("function/service values outside data profile"))
    }
    #[inline]
    fn serialize_option<T: CandidType + ?Sized>(self, value: Option<&T>) -> CResult<()> {
        self.budget.events += 1;
        if self.sources.op(&self.source) != Some(-18) {
            return Err(error("option callback disagrees with source type"));
        }
        match value {
            None => match self.phase {
                Phase::Derive(shape) => {
                    set_scalar(shape, -1);
                    Ok(())
                }
                Phase::Emit {
                    typ,
                    writer,
                    actual,
                } => {
                    set_scalar(actual, -1);
                    match typ.op {
                        -1 => Ok(()),
                        -18 => writer.add(&[0]).map_err(error),
                        _ => Err(error("option changed between visits")),
                    }
                }
            },
            Some(value) => {
                let child = self.sources.child(&self.source)?;
                self.budget.size(1);
                let phase = match self.phase {
                    Phase::Derive(shape) => {
                        set_unary(shape, -18);
                        Phase::Derive(&mut shape.children[0])
                    }
                    Phase::Emit {
                        typ,
                        writer,
                        actual,
                    } => {
                        if typ.op != -18 {
                            return Err(error("option changed between visits"));
                        }
                        writer.add(&[1]).map_err(error)?;
                        set_unary(actual, -18);
                        Phase::Emit {
                            typ: &typ.children[0],
                            writer,
                            actual: &mut actual.children[0],
                        }
                    }
                };
                visit(
                    value,
                    child,
                    self.sources,
                    self.budget,
                    phase,
                    self.depth + 1,
                    true,
                )
            }
        }
    }
    #[inline]
    fn serialize_vec(self, len: usize) -> CResult<Compound<'a, 'env>> {
        self.budget.events += 1;
        length(len)?;
        if self.sources.op(&self.source) != Some(-19) {
            return Err(error("vector callback disagrees with source type"));
        }
        let child = self.sources.child(&self.source)?;
        let binary = self.sources.op(&child) == Some(-5);
        self.budget.size(10);
        let phase = match self.phase {
            Phase::Derive(shape) => {
                set_unary(shape, -19);
                if len == 0 || binary {
                    set_scalar(&mut shape.children[0], if binary { -5 } else { -17 });
                }
                Phase::Derive(shape)
            }
            Phase::Emit {
                typ,
                writer,
                actual,
            } => {
                let typ = present(typ, writer, -19, self.depth)?;
                writer.integer(len, false).map_err(error)?;
                set_unary(actual, -19);
                if len == 0 || binary {
                    set_scalar(&mut actual.children[0], if binary { -5 } else { -17 });
                }
                Phase::Emit {
                    typ,
                    writer,
                    actual,
                }
            }
        };
        self.budget.compounds += 1;
        Ok(Compound {
            phase,
            source: child,
            sources: self.sources,
            budget: self.budget,
            depth: self.depth,
            kind: Kind::Vector(len),
            index: 0,
            target_index: 0,
            retained: 0,
            scratch: scalar(-17),
        })
    }
    #[inline]
    fn serialize_blob(self, value: &[u8]) -> CResult<()> {
        self.budget.events += 1;
        length(value.len())?;
        if self.sources.op(&self.source) != Some(-19) {
            return Err(error("blob callback disagrees with source type"));
        }
        let child = self.sources.child(&self.source)?;
        if self.sources.op(&child) != Some(-5) {
            return Err(error("blob requires Nat8 element type"));
        }
        if !value.is_empty() {
            self.budget.charge(value.len(), self.depth + 1)?;
        }
        match self.phase {
            Phase::Derive(shape) => {
                set_unary(shape, -19);
                set_scalar(&mut shape.children[0], -5);
                self.budget.size(value.len().saturating_add(10));
                Ok(())
            }
            Phase::Emit {
                typ,
                writer,
                actual,
            } => {
                set_unary(actual, -19);
                set_scalar(&mut actual.children[0], -5);
                let typ = present(typ, writer, -19, self.depth)?;
                if typ.children[0].op != -5 {
                    return Err(error("binary vector changed between visits"));
                }
                writer.integer(value.len(), false).map_err(error)?;
                writer.add(value).map_err(error)
            }
        }
    }
    #[inline]
    fn serialize_struct(self) -> CResult<Compound<'a, 'env>> {
        self.budget.events += 1;
        let kind = if self.sources.op(&self.source) == Some(-20) {
            self.sources.prepare_fields(&self.source)?;
            Kind::Record
        } else if self.sources.op(&self.source) == Some(-1) {
            Kind::Transparent(0)
        } else {
            Kind::Transparent(1)
        };
        let phase = match (kind, self.phase) {
            (Kind::Record, Phase::Derive(shape)) => {
                shape.op = -20;
                shape.labels.clear();
                Phase::Derive(shape)
            }
            (
                Kind::Record,
                Phase::Emit {
                    typ,
                    writer,
                    actual,
                },
            ) => {
                actual.op = -20;
                actual.labels.clear();
                Phase::Emit {
                    typ: present(typ, writer, -20, self.depth)?,
                    writer,
                    actual,
                }
            }
            (Kind::Transparent(0), Phase::Derive(shape)) => {
                set_scalar(shape, -1);
                Phase::Derive(shape)
            }
            (
                Kind::Transparent(0),
                Phase::Emit {
                    typ,
                    writer,
                    actual,
                },
            ) => {
                set_scalar(actual, -1);
                present(typ, writer, -1, self.depth)?;
                Phase::Emit {
                    typ,
                    writer,
                    actual,
                }
            }
            (_, phase) => phase,
        };
        self.budget.compounds += 1;
        Ok(Compound {
            source: self.source,
            sources: self.sources,
            budget: self.budget,
            phase,
            depth: self.depth,
            kind,
            index: 0,
            target_index: 0,
            retained: 0,
            scratch: scalar(-17),
        })
    }
    #[inline]
    fn serialize_variant(self, index: u64) -> CResult<Compound<'a, 'env>> {
        self.budget.events += 1;
        if self.sources.op(&self.source) != Some(-21) {
            return Err(error("variant callback disagrees with source type"));
        }
        self.sources.prepare_fields(&self.source)?;
        let index = usize::try_from(index).map_err(error)?;
        let field = self
            .sources
            .field(&self.source, index)
            .ok_or_else(|| error("invalid source variant index"))?;
        let source = self.sources.resolve(field.source)?;
        let label = field.id;
        let phase = match self.phase {
            Phase::Derive(shape) => {
                set_unary(shape, -21);
                shape.labels.push(label);
                Phase::Derive(&mut shape.children[0])
            }
            Phase::Emit {
                typ,
                writer,
                actual,
            } => {
                let typ = present(typ, writer, -21, self.depth)?;
                let index = typ
                    .labels
                    .binary_search(&label)
                    .map_err(|_| error("selected case changed between visits"))?;
                writer.integer(index, false).map_err(error)?;
                set_unary(actual, -21);
                actual.labels.push(label);
                Phase::Emit {
                    typ: &typ.children[index],
                    writer,
                    actual: &mut actual.children[0],
                }
            }
        };
        self.budget.size(10);
        self.budget.compounds += 1;
        Ok(Compound {
            source,
            sources: self.sources,
            budget: self.budget,
            phase,
            depth: self.depth + 1,
            kind: Kind::VariantPending,
            index: 0,
            target_index: 0,
            retained: 0,
            scratch: scalar(-17),
        })
    }
}

#[derive(Copy, Clone)]
enum Kind {
    Vector(usize),
    Record,
    Transparent(usize),
    VariantPending,
    VariantFull,
    VariantFields,
}
pub struct Compound<'a, 'env> {
    source: Source,
    sources: &'a mut Sources<'env>,
    budget: &'a mut Budget,
    phase: Phase<'a>,
    depth: usize,
    kind: Kind,
    index: usize,
    target_index: usize,
    retained: usize,
    scratch: Shape,
}
impl Compound<'_, '_> {
    #[inline(always)]
    fn element<T: CandidType + ?Sized>(&mut self, value: &T) -> CResult<()> {
        if matches!(self.kind, Kind::VariantPending) {
            let value_type = resolve(self.sources.env, &T::ty())?;
            self.kind = if self.sources.op(&self.source) == Some(-20)
                && !matches!(value_type.as_ref(), TypeInner::Unknown)
                && value_type != *self.sources.original(&self.source)
            {
                self.budget.visit(self.depth)?;
                self.sources.prepare_fields(&self.source)?;
                match &mut self.phase {
                    Phase::Derive(shape) => {
                        shape.op = -20;
                        shape.labels.clear();
                    }
                    Phase::Emit { actual, .. } => {
                        actual.op = -20;
                        actual.labels.clear();
                    }
                }
                Kind::VariantFields
            } else {
                Kind::VariantFull
            };
        }
        match self.kind {
            Kind::Vector(len) => {
                if self.index >= len {
                    return Err(error("too many vector element callbacks"));
                }
                self.index += 1;
                match &mut self.phase {
                    Phase::Derive(shape) => {
                        let child = if self.index == 1 {
                            &mut shape.children[0]
                        } else {
                            &mut self.scratch
                        };
                        visit(
                            value,
                            self.source.clone(),
                            self.sources,
                            self.budget,
                            Phase::Derive(child),
                            self.depth + 1,
                            true,
                        )?;
                        if self.index != 1 {
                            join_scratch(&mut shape.children[0], &mut self.scratch)
                                .map_err(error)?;
                        }
                        Ok(())
                    }
                    Phase::Emit {
                        typ,
                        writer,
                        actual,
                    } => {
                        let child = if self.index == 1 {
                            &mut actual.children[0]
                        } else {
                            &mut self.scratch
                        };
                        visit(
                            value,
                            self.source.clone(),
                            self.sources,
                            self.budget,
                            Phase::Emit {
                                typ: &typ.children[0],
                                writer,
                                actual: child,
                            },
                            self.depth + 1,
                            true,
                        )?;
                        if self.index != 1 {
                            join_scratch(&mut actual.children[0], &mut self.scratch)
                                .map_err(error)?;
                        }
                        Ok(())
                    }
                }
            }
            Kind::Record | Kind::VariantFields => self.field(value),
            Kind::Transparent(len) => {
                if self.index >= len {
                    return Err(error("too many transparent struct callbacks"));
                }
                self.index += 1;
                visit(
                    value,
                    self.source.clone(),
                    self.sources,
                    self.budget,
                    self.phase.borrow(),
                    self.depth,
                    false,
                )
            }
            Kind::VariantFull => {
                if self.index != 0 {
                    return Err(error("too many variant payload callbacks"));
                }
                self.index = 1;
                visit(
                    value,
                    self.source.clone(),
                    self.sources,
                    self.budget,
                    self.phase.borrow(),
                    self.depth,
                    true,
                )
            }
            Kind::VariantPending => Err(error("missing variant mode")),
        }
    }
    #[inline(always)]
    fn field<T: CandidType + ?Sized>(&mut self, value: &T) -> CResult<()> {
        if self.sources.op(&self.source) != Some(-20) {
            return Err(error("record payload metadata missing"));
        }
        let field = self
            .sources
            .field(&self.source, self.index)
            .ok_or_else(|| error("too many record field callbacks"))?;
        let id = field.id;
        self.index += 1;
        let field_source = self.sources.resolve(field.source)?;
        let optional = self.sources.op(&field_source) == Some(-18);
        match &mut self.phase {
            Phase::Derive(shape) => {
                let mut spare = scalar(-17);
                let child = shape.children.get_mut(self.retained).unwrap_or(&mut spare);
                visit(
                    value,
                    field_source,
                    self.sources,
                    self.budget,
                    Phase::Derive(child),
                    self.depth + 1,
                    true,
                )?;
                if !(optional && child.op == -1) {
                    shape.labels.push(id);
                    if self.retained == shape.children.len() {
                        shape.children.push(spare);
                    }
                    self.retained += 1;
                }
                Ok(())
            }
            Phase::Emit {
                typ,
                writer,
                actual,
            } => {
                while self.target_index < typ.labels.len() && typ.labels[self.target_index] < id {
                    emit_missing(writer, &typ.children[self.target_index])?;
                    self.target_index += 1;
                }
                if typ.labels.get(self.target_index) == Some(&id) {
                    let child = &typ.children[self.target_index];
                    self.target_index += 1;
                    let mut spare = scalar(-17);
                    let child_shape = actual.children.get_mut(self.retained).unwrap_or(&mut spare);
                    visit(
                        value,
                        field_source,
                        self.sources,
                        self.budget,
                        Phase::Emit {
                            typ: child,
                            writer,
                            actual: child_shape,
                        },
                        self.depth + 1,
                        true,
                    )?;
                    if !(optional && child_shape.op == -1) {
                        actual.labels.push(id);
                        if self.retained == actual.children.len() {
                            actual.children.push(spare);
                        }
                        self.retained += 1;
                    }
                    Ok(())
                } else {
                    let mut spare = scalar(-17);
                    let omitted = actual.children.get_mut(self.retained).unwrap_or(&mut spare);
                    visit(
                        value,
                        field_source,
                        self.sources,
                        self.budget,
                        Phase::Derive(omitted),
                        self.depth + 1,
                        true,
                    )?;
                    if !optional || omitted.op != -1 {
                        return Err(error("record field changed between visits"));
                    }
                    Ok(())
                }
            }
        }
    }
    fn finish(&mut self) -> CResult<()> {
        match self.kind {
            Kind::Vector(len) | Kind::Transparent(len) if self.index != len => {
                return Err(error("missing compound value callbacks"))
            }
            Kind::Record | Kind::VariantFields => {
                if self.sources.op(&self.source) != Some(-20) {
                    return Err(error("record payload metadata missing"));
                }
                if self.index != self.sources.fields_len(&self.source) {
                    return Err(error("missing record field callbacks"));
                }
                match &mut self.phase {
                    Phase::Derive(shape) => shape.children.truncate(self.retained),
                    Phase::Emit { actual, .. } => actual.children.truncate(self.retained),
                }
                if let Phase::Emit { typ, writer, .. } = &mut self.phase {
                    while self.target_index < typ.labels.len() {
                        emit_missing(writer, &typ.children[self.target_index])?;
                        self.target_index += 1;
                    }
                }
            }
            Kind::VariantPending => {
                self.budget.visit(self.depth)?;
                let shape = match self.sources.op(&self.source) {
                    Some(-1) => scalar(-1),
                    Some(-20)
                        if {
                            self.sources.prepare_fields(&self.source)?;
                            self.sources.fields_len(&self.source) == 0
                        } =>
                    {
                        Shape {
                            op: -20,
                            labels: vec![],
                            children: vec![],
                        }
                    }
                    _ => return Err(error("missing variant payload callbacks")),
                };
                match &mut self.phase {
                    Phase::Derive(target) => **target = shape,
                    Phase::Emit {
                        typ,
                        writer,
                        actual,
                    } => {
                        present(typ, writer, shape.op, self.depth)?;
                        **actual = shape;
                    }
                }
            }
            _ => {}
        }
        Ok(())
    }
}
fn emit_missing(writer: &mut Writer, typ: &Shape) -> CResult<()> {
    match typ.op {
        -1 => Ok(()),
        -18 => writer.add(&[0]).map_err(error),
        _ => Err(error("missing required derived field")),
    }
}
impl Drop for Compound<'_, '_> {
    fn drop(&mut self) {
        self.budget.compounds -= 1;
        if let Err(error) = self.finish() {
            self.budget.fail(error);
        }
    }
}
struct Blob<'a>(&'a [u8]);
impl CandidType for Blob<'_> {
    fn _ty() -> Type {
        TypeInner::Vec(TypeInner::Nat8.into()).into()
    }
    fn idl_serialize<S: Serializer>(&self, serializer: S) -> core::result::Result<(), S::Error> {
        serializer.serialize_blob(self.0)
    }
}
impl CandidCompound for Compound<'_, '_> {
    type Error = candid::Error;
    #[inline]
    fn serialize_element<T: CandidType + ?Sized>(&mut self, value: &T) -> CResult<()> {
        let result = self.element(value);
        if let Err(ref error) = result {
            self.budget.fail(error);
        }
        result
    }
    #[inline]
    fn serialize_blob(&mut self, value: &[u8]) -> CResult<()> {
        self.serialize_element(&Blob(value))
    }
    #[inline]
    fn try_write_raw_elements(&mut self, bytes: &[u8]) -> CResult<bool> {
        let result = (|| {
            let Kind::Vector(len) = self.kind else {
                return Ok(false);
            };
            let Some(op) = self.sources.op(&self.source) else {
                return Ok(false);
            };
            let width = match op {
                -2 | -5 | -9 => 1,
                -6 | -10 => 2,
                -7 | -11 | -13 => 4,
                -8 | -12 | -14 => 8,
                _ => return Ok(false),
            };
            if self.index != 0
                || bytes.len()
                    != len
                        .checked_mul(width)
                        .ok_or_else(|| error("vector byte range"))?
            {
                return Err(error("raw vector element count mismatch"));
            }
            if op == -2 && bytes.iter().any(|byte| *byte > 1) {
                return Err(error("invalid bool"));
            }
            if len != 0 {
                self.budget.charge(len, self.depth + 1)?;
            }
            match &mut self.phase {
                Phase::Derive(shape) => {
                    if len != 0 {
                        set_scalar(&mut shape.children[0], op);
                    }
                    self.budget.size(bytes.len());
                }
                Phase::Emit {
                    typ,
                    writer,
                    actual,
                } => {
                    if len != 0 {
                        set_scalar(&mut actual.children[0], op);
                    }
                    if len != 0 && typ.children[0].op != op {
                        return Err(error("vector element type changed between visits"));
                    }
                    if op == -13 {
                        for bytes in bytes.as_chunks::<4>().0 {
                            let bits = u32::from_le_bytes(*bytes);
                            writer
                                .add(
                                    &if f32::from_bits(bits).is_nan() {
                                        0x7fc0_0000u32
                                    } else {
                                        bits
                                    }
                                    .to_le_bytes(),
                                )
                                .map_err(error)?;
                        }
                    } else if op == -14 {
                        for bytes in bytes.as_chunks::<8>().0 {
                            let bits = u64::from_le_bytes(*bytes);
                            writer
                                .add(
                                    &if f64::from_bits(bits).is_nan() {
                                        0x7ff8_0000_0000_0000u64
                                    } else {
                                        bits
                                    }
                                    .to_le_bytes(),
                                )
                                .map_err(error)?;
                        }
                    } else {
                        writer.add(bytes).map_err(error)?;
                    }
                }
            }
            self.index = len;
            Ok(true)
        })();
        if let Err(ref error) = result {
            self.budget.fail(error);
        }
        result
    }
}

fn output(types: &[Shape], budget: &Budget) -> Result<Writer> {
    let mut writer = Writer(
        Vec::with_capacity(budget.bytes.saturating_add(128).min(MAX_BYTES)),
        false,
    );
    write_header(&mut writer, types)?;
    Ok(writer)
}
/// Direct Candid argument tuples. Unlike candid::utils::ArgumentEncoder this
/// trait does not depend on the upstream ordinary IDLBuilder.
pub trait ArgumentEncoder {
    #[doc(hidden)]
    fn encode_direct(&self) -> Result<Vec<u8>>;
}
impl ArgumentEncoder for () {
    fn encode_direct(&self) -> Result<Vec<u8>> {
        Ok(output(&[], &Budget::default())?.0)
    }
}
macro_rules! arguments {
    ($($name:ident:$index:tt),+) => {
        impl<$($name:CandidType),+> ArgumentEncoder for ($($name,)+) {
            fn encode_direct(&self) -> Result<Vec<u8>> {
                candid::types::internal::env_clear();
                let env = TypeEnv::new(); let mut budget = Budget::default();
                let source_types = vec![$($name::ty()),+]; let mut sources = Sources::new(&env);
                let indices: Vec<_> = source_types.iter().map(|ty| sources.add(ty)).collect(); let mut types = Vec::with_capacity(indices.len());
                $(let mut shape = scalar(-17);
                  visit(&self.$index, indices[$index].clone(), &mut sources, &mut budget, Phase::Derive(&mut shape), 0, true).map_err(|e| Error(e.to_string()))?;
                  types.push(shape);)+
                let mut writer = output(&types, &budget)?; let mut budget = Budget::default();
                $(let mut actual = scalar(-17);
                  visit(&self.$index, indices[$index].clone(), &mut sources, &mut budget, Phase::Emit { typ: &types[$index], writer: &mut writer, actual: &mut actual }, 0, true).map_err(|e| Error(e.to_string()))?;
                  if actual != types[$index] { return bad("serializer shape changed between visits"); })+
                Ok(writer.0)
            }
        }
    };
}
arguments!(A:0);
arguments!(A:0,B:1);
arguments!(A:0,B:1,C:2);
arguments!(A:0,B:1,C:2,D:3);
arguments!(A:0,B:1,C:2,D:3,E:4);
arguments!(A:0,B:1,C:2,D:3,E:4,F:5);
arguments!(A:0,B:1,C:2,D:3,E:4,F:5,G:6);
arguments!(A:0,B:1,C:2,D:3,E:4,F:5,G:6,H:7);
arguments!(A:0,B:1,C:2,D:3,E:4,F:5,G:6,H:7,I:8);
arguments!(A:0,B:1,C:2,D:3,E:4,F:5,G:6,H:7,I:8,J:9);
arguments!(A:0,B:1,C:2,D:3,E:4,F:5,G:6,H:7,I:8,J:9,K:10);
arguments!(A:0,B:1,C:2,D:3,E:4,F:5,G:6,H:7,I:8,J:9,K:10,L:11);
arguments!(A:0,B:1,C:2,D:3,E:4,F:5,G:6,H:7,I:8,J:9,K:10,L:11,M:12);
arguments!(A:0,B:1,C:2,D:3,E:4,F:5,G:6,H:7,I:8,J:9,K:10,L:11,M:12,N:13);
arguments!(A:0,B:1,C:2,D:3,E:4,F:5,G:6,H:7,I:8,J:9,K:10,L:11,M:12,N:13,O:14);
arguments!(A:0,B:1,C:2,D:3,E:4,F:5,G:6,H:7,I:8,J:9,K:10,L:11,M:12,N:13,O:14,P:15);

/// Encode a typed value directly in two callback visits. Custom CandidType
/// implementations must report an accurate type and remain stable between
/// visits. Existing ordinary decoders can decode the returned Candid bytes.
pub fn encode_one<T: CandidType>(value: T) -> Result<Vec<u8>> {
    encode_args((value,))
}
/// Encode the ordered members of a standard Rust argument tuple directly.
pub fn encode_args<T: ArgumentEncoder>(args: T) -> Result<Vec<u8>> {
    args.encode_direct()
}
/// Encode using explicit source metadata, for example an already annotated
/// dynamic IDLValue. The serializer callback order must match the source type.
pub fn encode_one_with_type<T: CandidType + ?Sized>(
    value: &T,
    env: &TypeEnv,
    source: &Type,
) -> Result<Vec<u8>> {
    encode_args_with_types(&[value], env, core::slice::from_ref(source))
}
/// Direct dynamic argument sequence, with one source type for each value.
pub fn encode_args_with_types<T: CandidType>(
    values: &[T],
    env: &TypeEnv,
    sources: &[Type],
) -> Result<Vec<u8>> {
    if values.len() != sources.len() {
        return bad("argument/type length mismatch");
    }
    length(values.len()).map_err(|e| Error(e.to_string()))?;
    let mut metadata = Sources::new(env);
    let mut budget = Budget::default();
    let mut types = Vec::with_capacity(values.len());
    for (value, source) in values.iter().zip(sources) {
        let source = metadata.add(source);
        let mut shape = scalar(-17);
        visit(
            value,
            source.clone(),
            &mut metadata,
            &mut budget,
            Phase::Derive(&mut shape),
            0,
            true,
        )
        .map_err(|e| Error(e.to_string()))?;
        types.push(shape);
    }
    let mut writer = output(&types, &budget)?;
    let mut budget = Budget::default();
    for ((value, source), typ) in values.iter().zip(sources).zip(&types) {
        let source = metadata.add(source);
        let mut actual = scalar(-17);
        visit(
            value,
            source.clone(),
            &mut metadata,
            &mut budget,
            Phase::Emit {
                typ,
                writer: &mut writer,
                actual: &mut actual,
            },
            0,
            true,
        )
        .map_err(|e| Error(e.to_string()))?;
        if actual != *typ {
            return bad("serializer shape changed between visits");
        }
    }
    Ok(writer.0)
}

#[cfg(test)]
mod cache_tests {
    use super::*;
    use candid::types::{Field, Label};

    struct Selected;
    impl CandidType for Selected {
        fn _ty() -> Type {
            TypeInner::Unknown.into()
        }
        fn idl_serialize<S: Serializer>(
            &self,
            serializer: S,
        ) -> core::result::Result<(), S::Error> {
            let mut variant = serializer.serialize_variant(0)?;
            variant.serialize_element(&7u8)
        }
    }

    #[test]
    fn unused_variant_children_are_not_cached() {
        // Every case owns a distinct Rc, so eagerly registering unused child
        // types would defeat both the node bound and the selected-value rule.
        let source: Type = TypeInner::Variant(
            (0..16_384)
                .map(|id| Field {
                    id: Label::Id(id).into(),
                    ty: TypeInner::Nat8.into(),
                })
                .collect(),
        )
        .into();
        let env = TypeEnv::new();
        let mut sources = Sources::new(&env);
        sources.enabled = true;
        let root = sources.add(&source);
        sources.prepare_fields(&root).unwrap();
        assert_eq!(sources.nodes.len(), 1);
        assert_eq!(sources.fields_cached, 0);
        let mut shape = scalar(-17);
        let mut budget = Budget::default();
        visit(
            &Selected,
            root.clone(),
            &mut sources,
            &mut budget,
            Phase::Derive(&mut shape),
            0,
            true,
        )
        .unwrap();
        assert_eq!(sources.nodes.len(), 2);
        assert_eq!(sources.fields_cached, 1);
        let actual = encode_one_with_type(&Selected, &env, &source).unwrap();
        let expected =
            encode_direct_args(&[DirectValue::Variant(0, Box::new(DirectValue::Nat8(7)))]).unwrap();
        assert_eq!(actual, expected);
        assert_eq!(canonicalize(&actual).unwrap(), actual);
    }

    #[test]
    fn metadata_cache_limits_fall_back_to_original_source() {
        let env = TypeEnv::new();
        let mut sources = Sources::new(&env);
        sources.enabled = true;
        for _ in 0..SOURCE_CACHE_NODES {
            let source = sources.add(&TypeInner::Nat8.into());
            assert!(matches!(source, Source::Cached(_)));
        }
        let fallback = sources.add(&TypeInner::Nat8.into());
        assert!(matches!(fallback, Source::Uncached(_)));
        let fallback = sources.resolve(fallback).unwrap();
        assert_eq!(sources.op(&fallback), Some(-5));
        assert_eq!(sources.nodes.len(), SOURCE_CACHE_NODES);

        let mut sources = Sources::new(&env);
        sources.enabled = true;
        let byte: Type = TypeInner::Nat8.into();
        let mut last = None;
        for _ in 0..=SOURCE_CACHE_FIELDS / 128 {
            let record: Type = TypeInner::Record(
                (0..128)
                    .map(|id| Field {
                        id: Label::Id(id).into(),
                        ty: byte.clone(),
                    })
                    .collect(),
            )
            .into();
            let source = sources.add(&record);
            sources.prepare_fields(&source).unwrap();
            last = Some(source);
        }
        assert_eq!(sources.fields_cached, SOURCE_CACHE_FIELDS);
        let last = last.unwrap();
        if let Source::Cached(index) = &last {
            assert!(sources.nodes[*index].fields.is_none());
        } else {
            panic!("test metadata unexpectedly exhausted the node cache");
        }
        let field = sources.field(&last, 127).unwrap();
        assert_eq!(field.id, 127);
        assert_eq!(sources.op(&field.source), Some(-5));
        assert_eq!(sources.fields_cached, SOURCE_CACHE_FIELDS);
    }

    #[test]
    fn bulk_byte_steps_do_not_activate_callback_metadata_cache() {
        let env = TypeEnv::new();
        let mut sources = Sources::new(&env);
        let source: Type = TypeInner::Vec(TypeInner::Nat8.into()).into();
        let source = sources.add(&source);
        let mut shape = scalar(-17);
        let mut budget = Budget::default();
        visit(
            &vec![0u8; 65_536],
            source,
            &mut sources,
            &mut budget,
            Phase::Derive(&mut shape),
            0,
            true,
        )
        .unwrap();
        assert_eq!(budget.steps, 65_537);
        assert!(budget.events < 32);
        assert!(!sources.enabled);
        assert!(sources.nodes.is_empty());
    }
}
