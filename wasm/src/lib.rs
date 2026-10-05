//! Standalone memory32 cx ABI. The linked Motoko RTS uses its own memory64 allocator.
#![no_std]
#![allow(static_mut_refs)]

extern crate alloc;

use alloc::alloc::{GlobalAlloc, Layout};
use alloc::borrow::Cow;
use alloc::boxed::Box;
use alloc::vec::Vec;
use core::arch::wasm32;

const MAX_INPUT: usize = 32 * 1024 * 1024;
const MAX_SCRATCH: usize = 256 * 1024 * 1024;
const PAGE: usize = 65536;

extern "C" {
    static __heap_base: u8;
}

static mut START: usize = 0;
static mut NEXT: usize = 0;
static mut RESULT_PTR: u32 = 0;
static mut RESULT_LEN: u32 = 0;

struct Scratch;

unsafe impl GlobalAlloc for Scratch {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        let aligned = match NEXT.checked_add(layout.align() - 1) {
            Some(n) => n & !(layout.align() - 1),
            None => wasm32::unreachable(),
        };
        let end = match aligned.checked_add(layout.size()) {
            Some(n) if n - START <= MAX_SCRATCH => n,
            _ => wasm32::unreachable(),
        };
        let current = wasm32::memory_size::<0>() * PAGE;
        if end > current {
            let pages = (end - current).div_ceil(PAGE);
            if wasm32::memory_grow::<0>(pages) == usize::MAX {
                wasm32::unreachable();
            }
        }
        NEXT = end;
        aligned as *mut u8
    }

    unsafe fn dealloc(&self, _ptr: *mut u8, _layout: Layout) {
        // Every allocation belongs to this operation. cx_reset reuses the arena.
    }
}

#[global_allocator]
static ALLOCATOR: Scratch = Scratch;

#[panic_handler]
fn panic(_info: &core::panic::PanicInfo) -> ! {
    wasm32::unreachable()
}

/// Start a new operation. All previous input/result pointers become invalid.
#[no_mangle]
pub unsafe extern "C" fn cx_reset() {
    START = core::ptr::addr_of!(__heap_base) as usize;
    NEXT = START;
    RESULT_PTR = 0;
    RESULT_LEN = 0;
}

/// Allocate an input span. Zero means an invalid length/uninitialized ABI.
#[no_mangle]
pub unsafe extern "C" fn cx_alloc(length: u32) -> u32 {
    if START == 0 || length as usize > MAX_INPUT {
        return 0;
    }
    ALLOCATOR.alloc(Layout::from_size_align_unchecked(
        (length as usize).max(1),
        1,
    )) as u32
}

unsafe fn error(message: &'static str) -> u32 {
    RESULT_PTR = message.as_ptr() as u32;
    RESULT_LEN = message.len() as u32;
    1
}

/// Normalize the complete input. 0 is success, 1 is a profile/input error.
/// The result span contains canonical bytes on success and UTF-8 error text otherwise.
/// Exceeding the 256 MiB scratch budget traps rather than discarding data.
#[no_mangle]
pub unsafe extern "C" fn cx_canonicalize(pointer: u32, length: u32) -> u32 {
    if length as usize > MAX_INPUT {
        return error("input byte limit");
    }
    let pointer = pointer as usize;
    if START == 0 || pointer < START || pointer == 0 {
        return error("input pointer outside allocated scratch");
    }
    match pointer.checked_add(length as usize) {
        Some(end) if end <= NEXT => (),
        _ => return error("input span outside allocated scratch"),
    }
    let input = core::slice::from_raw_parts(pointer as *const u8, length as usize);
    match candidx::canonicalize(input) {
        Ok(bytes) => {
            RESULT_PTR = bytes.as_ptr() as u32;
            RESULT_LEN = bytes.len() as u32;
            // Deallocation is a no-op; the bytes remain until the next reset.
            drop(bytes);
            0
        }
        Err(failure) => {
            RESULT_PTR = failure.0.as_ptr() as u32;
            RESULT_LEN = failure.0.len() as u32;
            drop(failure);
            1
        }
    }
}

const MAX_ITEMS: usize = 2_000_000;
const MAX_DEPTH: usize = 256;
const TYPED_NODE_BYTES: usize = 24;

unsafe fn typed_span(pointer: u32, length: usize) -> Result<&'static [u8], &'static str> {
    if length == 0 {
        return Ok(&[]);
    }
    let pointer = pointer as usize;
    if START == 0 || pointer < START || pointer == 0 {
        return Err("typed pointer outside allocated scratch");
    }
    match pointer.checked_add(length) {
        Some(end) if end <= NEXT => Ok(core::slice::from_raw_parts(pointer as *const u8, length)),
        _ => Err("typed span outside allocated scratch"),
    }
}

fn typed_word(bytes: &[u8], offset: usize) -> u32 {
    u32::from_le_bytes(bytes[offset..offset + 4].try_into().unwrap())
}

struct TypedReader {
    nodes: &'static [u8],
    steps: usize,
}
impl TypedReader {
    unsafe fn read(
        &mut self,
        index: u32,
        depth: usize,
    ) -> Result<candidx::DirectValue<'static>, &'static str> {
        use candidx::DirectValue as V;
        self.steps += 1;
        if self.steps > MAX_ITEMS || depth > MAX_DEPTH {
            return Err("value traversal limit");
        }
        let start = (index as usize)
            .checked_mul(TYPED_NODE_BYTES)
            .ok_or("typed node index range")?;
        let end = start
            .checked_add(TYPED_NODE_BYTES)
            .ok_or("typed node index range")?;
        let node = self.nodes.get(start..end).ok_or("typed node index range")?;
        let kind = typed_word(node, 0) as i32;
        let flags = typed_word(node, 4);
        let a = typed_word(node, 8);
        let b = typed_word(node, 12);
        let bits = u64::from(a) | u64::from(b) << 32;
        if typed_word(node, 16) != 0 || typed_word(node, 20) != 0 {
            return Err("typed reserved words must be zero");
        }
        if kind != -21 && flags & !1 != 0 {
            return Err("invalid typed flags");
        }
        if !matches!(kind, -4 | -19 | -21) && flags != 0 {
            return Err("invalid typed flags");
        }
        Ok(match kind {
            -1 if a == 0 && b == 0 => V::Null,
            -16 if a == 0 && b == 0 => V::Reserved,
            -2 if a <= 1 && b == 0 => V::Bool(a != 0),
            -3 => V::Nat(Cow::Borrowed(typed_span(a, b as usize)?)),
            -4 => V::Int {
                negative: flags != 0,
                magnitude: Cow::Borrowed(typed_span(a, b as usize)?),
            },
            -5 if bits <= u8::MAX as u64 => V::Nat8(bits as u8),
            -6 if bits <= u16::MAX as u64 => V::Nat16(bits as u16),
            -7 if b == 0 => V::Nat32(a),
            -8 => V::Nat64(bits),
            -9 if bits <= u8::MAX as u64 => V::Int8(bits as i8),
            -10 if bits <= u16::MAX as u64 => V::Int16(bits as i16),
            -11 if b == 0 => V::Int32(a as i32),
            -12 => V::Int64(bits as i64),
            -13 if b == 0 => V::Float32(f32::from_bits(a)),
            -14 => V::Float64(f64::from_bits(bits)),
            -15 => V::Text(
                core::str::from_utf8(typed_span(a, b as usize)?).map_err(|_| "invalid UTF-8")?,
            ),
            -24 => V::Principal(typed_span(a, b as usize)?),
            -26 => V::Blob(typed_span(a, b as usize)?),
            -18 if b == 0 => V::Option(if a == u32::MAX {
                None
            } else {
                Some(Box::new(self.read(a, depth + 1)?))
            }),
            -19 => {
                if b as usize > MAX_ITEMS {
                    return Err("collection length limit");
                }
                let children =
                    typed_span(a, (b as usize).checked_mul(4).ok_or("typed span range")?)?;
                let mut elements = Vec::with_capacity(b as usize);
                for i in 0..b as usize {
                    elements.push(self.read(typed_word(children, i * 4), depth + 1)?);
                }
                V::Vec {
                    elements,
                    binary: flags != 0,
                }
            }
            -20 => {
                if b as usize > MAX_ITEMS {
                    return Err("collection length limit");
                }
                let edges = typed_span(a, (b as usize).checked_mul(12).ok_or("typed span range")?)?;
                let mut fields = Vec::with_capacity(b as usize);
                for i in 0..b as usize {
                    let offset = i * 12;
                    let optional = typed_word(edges, offset + 8);
                    if optional > 1 {
                        return Err("invalid typed record optionality");
                    }
                    fields.push(candidx::DirectField {
                        id: typed_word(edges, offset),
                        optional: optional != 0,
                        value: self.read(typed_word(edges, offset + 4), depth + 1)?,
                    });
                }
                V::Record(fields)
            }
            -21 if b == 0 => V::Variant(flags, Box::new(self.read(a, depth + 1)?)),
            _ => return Err("unsupported or invalid typed node"),
        })
    }
}

/// Encode typed memory nodes directly. Nodes are 24-byte little-endian records
/// {kind:i32, flags:u32, a:u32, b:u32, reserved0:u32, reserved1:u32}; roots are
/// u32 node indices. Scalar bits occupy a/b; spans, child arrays and record
/// edges are allocated with cx_alloc. This accepts no ordinary Candid bytes.
#[no_mangle]
pub unsafe extern "C" fn cx_encode_typed(
    nodes_pointer: u32,
    node_count: u32,
    roots_pointer: u32,
    root_count: u32,
) -> u32 {
    if START == 0 {
        return error("typed encoder must be reset before use");
    }
    if node_count as usize > MAX_ITEMS || root_count as usize > MAX_ITEMS {
        return error("typed collection length limit");
    }
    let nodes = match typed_span(nodes_pointer, node_count as usize * TYPED_NODE_BYTES) {
        Ok(span) => span,
        Err(message) => return error(message),
    };
    let roots = match typed_span(roots_pointer, root_count as usize * 4) {
        Ok(span) => span,
        Err(message) => return error(message),
    };
    let mut reader = TypedReader { nodes, steps: 0 };
    let mut values = Vec::with_capacity(root_count as usize);
    for i in 0..root_count as usize {
        match reader.read(typed_word(roots, i * 4), 0) {
            Ok(value) => values.push(value),
            Err(message) => return error(message),
        }
    }
    match candidx::encode_direct_args(&values) {
        Ok(bytes) => {
            RESULT_PTR = bytes.as_ptr() as u32;
            RESULT_LEN = bytes.len() as u32;
            drop(bytes);
            0
        }
        Err(failure) => {
            RESULT_PTR = failure.0.as_ptr() as u32;
            RESULT_LEN = failure.0.len() as u32;
            drop(failure);
            1
        }
    }
}

#[no_mangle]
pub unsafe extern "C" fn cx_result_ptr() -> u32 {
    RESULT_PTR
}

#[no_mangle]
pub unsafe extern "C" fn cx_result_len() -> u32 {
    RESULT_LEN
}

#[no_mangle]
pub unsafe extern "C" fn cx_scratch_used() -> u32 {
    NEXT.saturating_sub(START) as u32
}

#[no_mangle]
pub extern "C" fn cx_memory_pages() -> u32 {
    wasm32::memory_size::<0>() as u32
}
