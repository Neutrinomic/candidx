// Compiler/RTS integration prototype. The compiler must expose this primitive.
import Prim "mo:⛔";
module {
  public func canonicalize(raw : Blob) : Blob {
    Prim.canonicalizeCandid(raw)
  };
}
