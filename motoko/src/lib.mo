// CandidX canonical values. Native Motoko; no hashing or generic block Value API.
import Prim "mo:⛔";
import Array "mo:core/Array";
import List "mo:core/List";

module {
  public let profile = "cx";
  public type Result = { #ok : Blob; #err : Text };
  type Node = { op : Int; labels : [Nat]; edges : [Int] };
  type Value = {
    op : Int; raw : Blob; labels : [Nat]; items : [Value];
    // A negative element kind stores a fixed-width vector as an input span;
    // zero stores its elements in items. raw only replaces NaN scalar bytes.
    element : Int;
    offset : Nat; length : Nat
  };
  type Shape = { op : Int; labels : [Nat]; children : [Shape] };
  let maxBytes = 33_554_432;
  let maxItems = 2_000_000;
  let maxTypes = 1024;
  let maxDepth = 256;
  let nullValue : Value = { op = -1; raw = ""; labels = []; items = []; element = 0; offset = 0; length = 0 };
  let reservedValue : Value = { op = -16; raw = ""; labels = []; items = []; element = 0; offset = 0; length = 0 };
  let emptyRecord : Value = { op = -20; raw = ""; labels = []; items = []; element = 0; offset = 0; length = 0 };
  // Build each value directly rather than copying a prototype record.
  func groupValue(op : Int, labels : [Nat], items : [Value]) : Value = {
    op; raw = ""; labels; items; element = 0; offset = 0; length = 0
  };
  func fixedValue(element : Int, offset : Nat, length : Nat) : Value = {
    op = -19; raw = ""; labels = []; items = []; element; offset; length
  };
  func scalarValue(op : Int, raw : Blob, offset : Nat, length : Nat) : Value = {
    op; raw; labels = []; items = []; element = 0; offset; length
  };
  // Immutable primitive shapes are shared across record/vector elements.
  let scalars : [Shape] = [
    { op = 0; labels = []; children = [] },
    { op = -1; labels = []; children = [] },
    { op = -2; labels = []; children = [] },
    { op = -3; labels = []; children = [] },
    { op = -4; labels = []; children = [] },
    { op = -5; labels = []; children = [] },
    { op = -6; labels = []; children = [] },
    { op = -7; labels = []; children = [] },
    { op = -8; labels = []; children = [] },
    { op = -9; labels = []; children = [] },
    { op = -10; labels = []; children = [] },
    { op = -11; labels = []; children = [] },
    { op = -12; labels = []; children = [] },
    { op = -13; labels = []; children = [] },
    { op = -14; labels = []; children = [] },
    { op = -15; labels = []; children = [] },
    { op = -16; labels = []; children = [] },
    { op = -17; labels = []; children = [] },
    { op = -18; labels = []; children = [] },
    { op = -19; labels = []; children = [] },
    { op = -20; labels = []; children = [] },
    { op = -21; labels = []; children = [] },
    { op = -22; labels = []; children = [] },
    { op = -23; labels = []; children = [] },
    { op = -24; labels = []; children = [] }
  ];
  func scalar(op : Int) : Shape = scalars[Prim.abs(op)];
  func unary(op : Int, child : Shape) : Shape = {
    op; labels = []; children = [child]
  };
  let blobShape : Shape = { op = -19; labels = []; children = [{ op = -5; labels = []; children = [] }] };
  let someBlobShape : Shape = { op = -18; labels = []; children = [blobShape] };
  func absent(nodes : [Node], edge : Int, child : Value) : Bool =
    edge >= 0 and nodes[Prim.abs(edge)].op == -18 and child.op == -1;
  func compound(op : Int) : Bool = op >= -21 and op <= -18;
  func fixedPrimitive(op : Int) : Bool =
    (op >= -14 and op <= -5) or op == -2 or op == -1 or op == -16;
  func fixedWidth(op : Int) : Nat {
    if (op == -1 or op == -16) 0
    else if (op == -2) 1
    else if (op == -13) 4
    else if (op == -14) 8
    else 2 ** (Prim.abs((Prim.abs(op) : Int) - 5) % 4)
  };
  func isNaN(data : Blob, offset : Nat, size : Nat) : Bool {
    if (size == 4) {
      Prim.nat8ToNat(data[offset + 3]) % 128 == 127 and
      data[offset + 2] >= 128 and
      (Prim.nat8ToNat(data[offset + 2]) % 128 != 0 or
        data[offset + 1] != 0 or data[offset] != 0)
    } else {
      if (Prim.nat8ToNat(data[offset + 7]) % 128 != 127 or
        data[offset + 6] < 240) return false;
      if (Prim.nat8ToNat(data[offset + 6]) % 16 != 0) return true;
      var i = 0;
      while (i < 6) {
        if (data[offset + i] != 0) return true;
        i += 1
      };
      false
    }
  };

  func validUtf8(data : Blob, offset : Nat, size : Nat) : Bool {
    var i = offset;
    let end = offset + size;
    while (i < end) {
      let first = data[i];
      if (first < 128) i += 1
      else {
        var width = 2;
        var low : Nat8 = 128;
        var high : Nat8 = 191;
        if (first >= 194 and first <= 223) {}
        else if (first >= 224 and first <= 239) {
          width := 3;
          if (first == 224) low := 160;
          if (first == 237) high := 159
        } else if (first >= 240 and first <= 244) {
          width := 4;
          if (first == 240) low := 144;
          if (first == 244) high := 143
        } else return false;
        if (width > Prim.abs((end : Int) - i)) return false;
        let second = data[i + 1];
        if (second < low or second > high) return false;
        var j = 2;
        while (j < width) {
          let next = data[i + j];
          if (next < 128 or next > 191) return false;
          j += 1
        };
        i += width
      }
    };
    true
  };


  /// Normalize a complete ordinary Candid message before typed projection.
  /// Call with `to_candid(block)`; existing `from_candid` decodes the result.
  /// Explicit errors cover unsupported data, malformed bytes, and limits.
  public func canonicalize(input : Blob) : Result {
    if (input.size() > maxBytes) return #err("input byte limit");
    var failure : ?Text = null;
    func fail(message : Text) { if (failure == null) failure := ?message };
    func failed() : Bool = failure != null;
    let data = input;
    var pos = 0;
    func skip(n : Nat) : Nat {
      let offset = pos;
      if (not failed()) {
        if (n > Prim.abs((data.size() : Int) - pos)) fail("truncated message")
        else pos += n
      };
      offset
    };
    func take(n : Nat) : Blob {
      let offset = skip(n);
      if (failed()) return "";
      Prim.arrayToBlob(Array.tabulate<Nat8>(n, func(i) { data[offset + i] }))
    };
    func byte() : Nat {
      if (failed()) return 0;
      if (pos >= data.size()) { fail("truncated message"); return 0 };
      let b = Prim.nat8ToNat(data[pos]); pos += 1; b
    };
    func integer(signed : Bool, limit : Nat) : Int {
      var acc = 0; var factor = 1; var i = 0;
      while (i < limit and not failed()) {
        let b = byte();
        acc += (b % 128) * factor;
        factor *= 128;
        if (b < 128) {
          return if (signed and b >= 64) (acc : Int) - factor else acc
        };
        i += 1;
      };
      if (not failed()) fail("integer length limit");
      0
    };
    func scalarInteger(signed : Bool) : Nat {
      let start = pos; var i = 0; var done = false;
      while (i < 4096 and not failed() and not done) {
        done := byte() < 128; i += 1
      };
      if (failed()) return 0;
      if (not done) { fail("integer length limit"); return 0 };
      var end = pos;
      label trim while (Prim.abs((end : Int) - start) > 1) {
        let last = Prim.nat8ToNat(data[end - 1]) % 128;
        let previous = Prim.nat8ToNat(data[end - 2]);
        if ((not signed and last == 0) or (signed and
          ((last == 0 and previous % 128 < 64) or
           (last == 127 and previous % 128 >= 64)))) end -= 1
        else break trim
      };
      Prim.abs((end : Int) - start)
    };
    func length() : Nat {
      let n = Prim.abs(integer(false, 10));
      if (n > maxItems) { fail("collection length limit"); return 0 };
      n
    };
    func refs() : [Int] = Array.tabulate<Int>(length(), func(_) {
      integer(true, 10)
    });
    func text() : Blob {
      let bytes = take(length());
      if (Prim.decodeUtf8(bytes) == null) fail("invalid UTF-8");
      bytes
    };
    if (take(4) != "DIDL") fail("missing DIDL magic");
    let count = length();
    if (count > maxTypes) fail("input type-table limit");
    if (failed()) {
      switch failure { case (?e) return #err(e); case null {} }
    };
    let nodes = Array.tabulate<Node>(count, func(_) {
      let op = integer(true, 10);
      let labels = List.empty<Nat>(); let edges = List.empty<Int>();
      if (op == -18 or op == -19) List.add(edges, integer(true, 10))
      else if (op == -20 or op == -21) {
        let n = length(); var j = 0; var previous : ?Nat = null;
        while (j < n and not failed()) {
          let id = Prim.abs(integer(false, 10));
          if (id > 0xffff_ffff) fail("field ID range");
          switch previous {
            case (?p) { if (id <= p) fail("unordered field IDs") };
            case null {};
          };
          previous := ?id;
          List.add(labels, id); List.add(edges, integer(true, 10)); j += 1;
        }
      } else if (op == -22) {
        let args = refs(); let rets = refs();
        for (e in args.values()) List.add(edges, e);
        for (e in rets.values()) List.add(edges, e);
        let modes = Prim.blobToArray(take(length()));
        if (modes.size() > 1) fail("invalid function annotations")
        else if (modes.size() == 1) {
          if (modes[0] < 1 or modes[0] > 3 or
            (modes[0] == 2 and rets.size() != 0))
            fail("invalid function annotations")
        }
      } else if (op == -23) {
        let n = length(); var j = 0; var previous : ?Blob = null;
        while (j < n and not failed()) {
          let name = text();
          switch previous {
            case (?p) { if (Prim.blobCompare(p, name) >= 0)
              fail("unordered service methods") };
            case null {};
          };
          previous := ?name;
          List.add(edges, integer(true, 10)); j += 1;
        }
      } else fail("unsupported compound type");
      { op; labels = List.toArray(labels); edges = List.toArray(edges) }
    });
    let roots = refs();
    func check(e : Int) {
      if ((e >= 0 and e >= nodes.size()) or
        (e < 0 and not (e >= -17 and e <= -1) and e != -24))
        fail("invalid type reference")
    };
    for (e in roots.values()) check(e);
    for (n in nodes.values()) for (e in n.edges.values()) check(e);
    if (not failed()) for (n in nodes.values()) {
      if (n.op == -23) for (e in n.edges.values()) {
        if (e < 0 or nodes[Prim.abs(e)].op != -22) fail("method must be func")
      }
    };
    var steps = 0;
    func read(ref_ : Int, depth : Nat) : Value {
      if (failed()) return nullValue;
      steps += 1;
      if (steps > maxItems or depth > maxDepth) {
        fail("value traversal limit"); return nullValue
      };
      if (ref_ >= 0) {
        let node = nodes[Prim.abs(ref_)];
        if (node.op == -18) {
          let tag = byte();
          if (tag == 0) return nullValue;
          if (tag != 1) { fail("invalid option tag"); return nullValue };
          return groupValue(-18, [], [read(node.edges[0], depth + 1)])
        };
        if (node.op == -19) {
          let n = length();
          let element = node.edges[0];
          if (fixedPrimitive(element) and (n != 0 or element == -5)) {
            steps += n;
            if (steps > maxItems or (n != 0 and depth + 1 > maxDepth)) {
              fail("value traversal limit"); return nullValue
            };
            let offset = skip(n * fixedWidth(element));
            if (element == -2 and not failed()) {
              var i = 0;
              while (i < n and not failed()) {
                if (data[offset + i] > 1) fail("invalid bool");
                i += 1
              }
            };
            return fixedValue(element, offset, n)
          };
          return groupValue(-19, [], Array.tabulate<Value>(n, func(_) {
            read(node.edges[0], depth + 1)
          }))
        };
        if (node.op == -20) {
          let n = node.edges.size();
          // Small records use exact arrays; wide optional records keep the
          // growable List path so all-absent input does not allocate n slots.
          if (n <= 16) {
            var kept = 0;
            let all = Array.tabulate<Value>(n, func(i) {
              let child = read(node.edges[i], depth + 1);
              if (not absent(nodes, node.edges[i], child)) kept += 1;
              child
            });
            if (failed()) return nullValue;
            if (kept == n) return groupValue(-20, node.labels, all);
            if (kept == 0) return emptyRecord;
            var index = 0;
            let labels = Array.tabulate<Nat>(kept, func(_) {
              while (absent(nodes, node.edges[index], all[index])) index += 1;
              let id = node.labels[index]; index += 1; id
            });
            index := 0;
            let items = Array.tabulate<Value>(kept, func(_) {
              while (absent(nodes, node.edges[index], all[index])) index += 1;
              let child = all[index]; index += 1; child
            });
            return groupValue(-20, labels, items)
          };
          var optional = false;
          label check for (edge in node.edges.values()) {
            if (edge >= 0 and nodes[Prim.abs(edge)].op == -18) {
              optional := true; break check
            }
          };
          if (not optional) return groupValue(-20, node.labels,
            Array.map<Int, Value>(node.edges, func(e) { read(e, depth + 1) }));
          let fields = List.empty<Nat>(); let items = List.empty<Value>();
          var i = 0;
          while (i < node.edges.size() and not failed()) {
            let child = read(node.edges[i], depth + 1);
            let edge = node.edges[i];
            let missing = absent(nodes, edge, child);
            if (not missing) {
              List.add(fields, node.labels[i]); List.add(items, child)
            };
            i += 1;
          };
          return groupValue(-20, List.toArray(fields), List.toArray(items))
        };
        if (node.op == -21) {
          let index = length();
          if (index >= node.edges.size()) {
            fail("invalid variant index"); return nullValue
          };
          return groupValue(-21, [node.labels[index]], [read(node.edges[index], depth + 1)])
        };
        fail("function/service values outside data profile"); return nullValue
      };
      if (ref_ == -1) return nullValue;
      if (ref_ == -16) return reservedValue;
      if (ref_ == -17) { fail("empty has no values"); return nullValue };
      var raw : Blob = "";
      var offset = pos;
      var size = 0;
      if (ref_ == -2) {
        let b = byte(); if (b > 1) fail("invalid bool"); size := 1
      } else if (ref_ == -3 or ref_ == -4) {
        size := scalarInteger(ref_ == -4)
      } else if (ref_ >= -12 and ref_ <= -5) {
        size := 2 ** (Prim.abs((Prim.abs(ref_) : Int) - 5) % 4);
        ignore skip(size)
      } else if (ref_ == -13 or ref_ == -14) {
        size := fixedWidth(ref_);
        ignore skip(size);
        if (not failed() and isNaN(data, offset, size)) {
          raw := if (size == 4) "\00\00\c0\7f"
          else "\00\00\00\00\00\00\f8\7f"
        }
      } else if (ref_ == -15) {
        size := length(); offset := skip(size);
        if (not failed() and not validUtf8(data, offset, size)) fail("invalid UTF-8")
      } else if (ref_ == -24) {
        if (byte() != 1) fail("opaque principal");
        size := length(); offset := pos;
        if (size > 29) fail("principal length exceeds 29")
        else ignore skip(size)
      } else fail("unsupported primitive");
      scalarValue(ref_, raw, offset, size)
    };
    let values = Array.map<Int, Value>(roots, func(e) { read(e, 0) });
    if (pos != data.size()) fail("trailing payload");
    func join(a : Shape, b : Shape, depth : Nat) : Shape {
      if (failed()) return scalar(-1);
      if (depth > maxDepth) { fail("type nesting limit"); return scalar(-1) };
      if (a == b or b.op == -17) return a;
      if (a.op == -17) return b;
      if (a.op == -1) return if (b.op == -18) b else unary(-18, b);
      if (b.op == -1) return if (a.op == -18) a else unary(-18, a);
      if (a.op == -18 or b.op == -18) return unary(-18, join(
        if (a.op == -18) a.children[0] else a,
        if (b.op == -18) b.children[0] else b, depth + 1));
      if (a.op != b.op or not (a.op == -19 or a.op == -20 or a.op == -21)) {
        fail("incompatible value shapes"); return scalar(-1)
      };
      if (a.op == -19) return unary(-19,
        join(a.children[0], b.children[0], depth + 1));
      let labels = List.empty<Nat>(); let children = List.empty<Shape>();
      var i = 0; var j = 0;
      while ((i < a.labels.size() or j < b.labels.size()) and not failed()) {
        if (j >= b.labels.size() or
          (i < a.labels.size() and a.labels[i] < b.labels[j])) {
          List.add(labels, a.labels[i]);
          List.add(children, if (a.op == -21) a.children[i] else
            join(a.children[i], scalar(-1), depth + 1)); i += 1
        } else if (i >= a.labels.size() or b.labels[j] < a.labels[i]) {
          List.add(labels, b.labels[j]);
          List.add(children, if (a.op == -21) b.children[j] else
            join(scalar(-1), b.children[j], depth + 1)); j += 1
        } else {
          List.add(labels, a.labels[i]);
          List.add(children, join(a.children[i], b.children[j], depth + 1));
          i += 1; j += 1
        }
      };
      { op = a.op; labels = List.toArray(labels);
        children = List.toArray(children) }
    };
    func shape(v : Value, depth : Nat) : Shape {
      if (failed()) return scalar(-1);
      if (depth > maxDepth) { fail("type nesting limit"); return scalar(-1) };
      if (v.op == -18) {
        if (v.items[0].op == -19 and v.items[0].element == -5) return someBlobShape;
        return unary(-18, shape(v.items[0], depth + 1))
      };
      if (v.op == -19) {
        if (v.element == -5) return blobShape;
        if (v.element != 0) return unary(-19, scalar(v.element));
        var element = scalar(-17);
        for (item in v.items.values()) element := join(element,
          shape(item, depth + 1), 0);
        return unary(-19, element)
      };
      if ((v.op == -20 or v.op == -21) and v.items.size() == 0) return scalar(v.op);
      if (v.op == -20 or v.op == -21) return { op = v.op; labels = v.labels;
        children = Array.map<Value, Shape>(v.items, func(c) {
          shape(c, depth + 1)
        }) };
      scalar(v.op)
    };
    let types = Array.map<Value, Shape>(values, func(v) { shape(v, 0) });
    let table = List.empty<Shape>();
    func intern(t : Shape) : Int {
      if (not compound(t.op)) return t.op;
      if (failed()) return 0;
      var i = 0;
      while (i < List.size(table)) {
        if (List.at(table, i) == t) return i; i += 1
      };
      if (i >= maxTypes) { fail("output type-table limit"); return 0 };
      List.add(table, t);
      for (child in t.children.values()) { ignore intern(child) };
      i
    };
    let args = Array.map<Shape, Int>(types, intern);
    // Count first, then write once into an exactly sized buffer. No byte chunks
    // or temporary LEB blobs are retained alongside the output.
    var outputSize = 0;
    var bytes : [var Nat8] = [var];
    var writing = false;
    var matching = true;
    func reserve(length : Nat) : Nat {
      let start = outputSize;
      if (not failed()) {
        if (length > Prim.abs((maxBytes : Int) - outputSize))
          fail("output byte limit")
        else outputSize += length
      };
      start
    };
    func add(source : Blob) {
      let start = reserve(source.size());
      if (not writing and matching and not failed()) {
        if (start > data.size() or source.size() > Prim.abs((data.size() : Int) - start)) matching := false
        else {
          var i = 0;
          while (i < source.size() and matching) {
            if (source[i] != data[start + i]) matching := false;
            i += 1
          }
        }
      };
      if (writing and not failed()) {
        var i = 0;
        while (i < source.size()) { bytes[start + i] := source[i]; i += 1 }
      }
    };
    func span(offset : Nat, length : Nat) {
      let start = reserve(length);
      // Aligned input spans are identical without reading their bytes. Once
      // output shifts, use the writer; do not scan large shifted payloads.
      if (not writing and matching and length != 0 and offset != start) matching := false;
      if (writing and not failed()) {
        var i = 0;
        while (i < length) { bytes[start + i] := data[offset + i]; i += 1 }
      }
    };
    func floats(offset : Nat, count : Nat, size : Nat) {
      let start = reserve(count * size);
      if (not writing and matching and count != 0 and not failed()) {
        if (offset != start) matching := false
        else {
          let canonical : Blob = if (size == 4) "\00\00\c0\7f"
            else "\00\00\00\00\00\00\f8\7f";
          var i = 0;
          while (i < count and matching) {
            let source = offset + i * size;
            if (isNaN(data, source, size)) {
              var j = 0;
              while (j < size and matching) {
                if (data[source + j] != canonical[j]) matching := false;
                j += 1
              }
            };
            i += 1
          }
        }
      };
      if (writing and not failed()) {
        var i = 0;
        while (i < count) {
          let source = offset + i * size;
          let target = start + i * size;
          let nan = isNaN(data, source, size);
          var j = 0;
          while (j < size) {
            bytes[target + j] := if (nan) {
              if (size == 4) ("\00\00\c0\7f" : Blob)[j]
              else ("\00\00\00\00\00\00\f8\7f" : Blob)[j]
            } else data[source + j];
            j += 1
          };
          i += 1
        }
      }
    };
    func octet(b : Nat8) {
      let start = reserve(1);
      if (not writing and matching and not failed()) {
        if (start >= data.size() or data[start] != b) matching := false
      };
      if (writing and not failed()) bytes[start] := b
    };
    func num(n : Int, signed : Bool) {
      var rest = n;
      label write loop {
        let rem = rest % 128;
        let b = Prim.abs(if (rem < 0) rem + 128 else rem);
        rest := (rest - b) / 128;
        let done = if (signed) {
          (rest == 0 and b < 64) or (rest == -1 and b >= 64)
        } else rest == 0;
        octet(Prim.natToNat8(b + (if (done) 0 else 128)));
        if (done or failed()) break write;
      }
    };
    func header() {
      add("DIDL"); num(List.size(table), false);
      var i = 0;
      while (i < List.size(table) and not failed()) {
        let t = List.at(table, i);
        num(t.op, true);
        if (t.op == -18 or t.op == -19) num(intern(t.children[0]), true)
        else {
          num(t.labels.size(), false); var j = 0;
          while (j < t.labels.size()) {
            num(t.labels[j], false); num(intern(t.children[j]), true); j += 1
          }
        };
        i += 1
      };
      num(args.size(), false); for (a in args.values()) num(a, true)
    };
    func emit(v : Value, t : Shape, depth : Nat) {
      if (failed()) return;
      if (depth > maxDepth) { fail("emitted value nesting limit"); return };
      if (t.op == -18) {
        if (v.op == -1) octet(0)
        else { octet(1); emit(if (v.op == -18) v.items[0] else v,
          t.children[0], depth + 1) }
      } else if (t.op == -19 and v.op == -19) {
        if (v.element != 0) {
          if (t.children[0].op != v.element) { fail("invalid compact vector shape"); return };
          if (v.element != -5 and v.length != 0 and depth >= maxDepth) {
            fail("emitted value nesting limit"); return
          };
          num(v.length, false);
          if (v.element == -13 or v.element == -14)
            floats(v.offset, v.length, fixedWidth(v.element))
          else span(v.offset, v.length * fixedWidth(v.element))
        } else {
          num(v.items.size(), false);
          for (c in v.items.values()) emit(c, t.children[0], depth + 1)
        }
      } else if (t.op == -20 and v.op == -20) {
        var i = 0; var j = 0;
        while (i < t.labels.size() and not failed()) {
          while (j < v.labels.size() and v.labels[j] < t.labels[i]) j += 1;
          let child = if (j < v.labels.size() and v.labels[j] == t.labels[i])
            v.items[j] else nullValue;
          emit(child, t.children[i], depth + 1); i += 1
        }
      } else if (t.op == -21 and v.op == -21) {
        var i = 0;
        while (i < t.labels.size() and t.labels[i] != v.labels[0]) i += 1;
        if (i >= t.labels.size()) { fail("missing derived variant"); return };
        num(i, false); emit(v.items[0], t.children[i], depth + 1)
      } else if (v.op == t.op and not compound(t.op) and t.op != -17) {
        if (v.op == -3 or v.op == -4) {
          if (v.length == 0) { fail("invalid scalar integer"); return };
          let prefix = Prim.abs((v.length : Int) - 1);
          span(v.offset, prefix);
          octet(Prim.natToNat8(Prim.nat8ToNat(data[v.offset + prefix]) % 128))
        } else if (v.op == -15) {
          num(v.length, false); span(v.offset, v.length)
        } else if (v.op == -24) {
          octet(1); num(v.length, false); span(v.offset, v.length)
        } else if (v.raw.size() != 0) add(v.raw)
        else span(v.offset, v.length)
      } else fail("cannot emit at derived type")
    };
    func message() {
      header();
      var i = 0;
      while (i < values.size() and not failed()) {
        emit(values[i], types[i], 0); i += 1
      }
    };
    message();
    if (not failed() and matching and outputSize == data.size()) return #ok(input);
    if (not failed()) {
      bytes := Prim.Array_init<Nat8>(outputSize, 0);
      outputSize := 0;
      writing := true;
      message()
    };
    switch failure {
      case (?e) #err(e);
      case null #ok(Prim.arrayMutToBlob(bytes));
    }
  };
}
