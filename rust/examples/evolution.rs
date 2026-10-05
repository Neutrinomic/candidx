use candid::{CandidType, Deserialize, Nat};
use std::fmt::Debug;

#[derive(CandidType, Deserialize, Clone, Debug, PartialEq)]
struct Old {
    a: String,
}
#[derive(CandidType, Deserialize, Clone, Debug, PartialEq)]
struct New {
    a: String,
    b: Option<String>,
}
#[derive(CandidType, Deserialize, Clone, Debug, PartialEq)]
struct Newer {
    a: String,
    b: Option<String>,
    c: Option<Vec<Nat>>,
}
#[derive(CandidType, Deserialize, Clone, Debug, PartialEq)]
struct NestedOption {
    a: String,
    b: Option<Option<String>>,
}
#[derive(CandidType, Deserialize, Clone, Debug, PartialEq)]
enum Choice {
    #[serde(rename = "a")]
    A,
    #[serde(rename = "b")]
    B(Nat),
}
#[derive(CandidType, Deserialize, Clone, Debug, PartialEq)]
enum MoreChoice {
    #[serde(rename = "a")]
    A,
    #[serde(rename = "b")]
    B(Nat),
    #[serde(rename = "c")]
    C(String),
}
#[derive(CandidType, Deserialize, Clone, Debug, PartialEq)]
struct OuterOld {
    inner: Old,
    items: Vec<Choice>,
}
#[derive(CandidType, Deserialize, Clone, Debug, PartialEq)]
struct OuterNew {
    inner: New,
    items: Vec<MoreChoice>,
    extra: Option<Nat>,
}

fn emit<T>(name: &str, value: T)
where
    T: CandidType + for<'de> serde::Deserialize<'de> + Clone + PartialEq + Debug,
{
    let canonical = candidx::encode_one(value.clone()).unwrap();
    let raw = candid::encode_one(value.clone()).unwrap();
    assert_eq!(
        candid::decode_one::<T>(&canonical).unwrap(),
        value,
        "{name}"
    );
    assert_eq!(candidx::canonicalize(&canonical).unwrap(), canonical);
    println!("raw:{name} {}", hex::encode(raw));
    println!("canonical:{name} {}", hex::encode(canonical));
}
fn old(a: &str) -> Old {
    Old { a: a.into() }
}
fn new(a: &str, b: Option<&str>) -> New {
    New {
        a: a.into(),
        b: b.map(String::from),
    }
}
fn newer(a: &str, b: Option<&str>) -> Newer {
    Newer {
        a: a.into(),
        b: b.map(String::from),
        c: None,
    }
}
fn main() {
    emit("evolution-record-old", old("abc"));
    emit("evolution-record-new", new("abc", None));
    emit("evolution-record-newer", newer("abc", None));
    emit("evolution-present-empty", new("abc", Some("")));
    emit("evolution-present-text", new("abc", Some("new data")));
    emit(
        "evolution-nested-none",
        NestedOption {
            a: "abc".into(),
            b: None,
        },
    );
    emit(
        "evolution-nested-some-none",
        NestedOption {
            a: "abc".into(),
            b: Some(None),
        },
    );
    emit(
        "evolution-nested-some-some",
        NestedOption {
            a: "abc".into(),
            b: Some(Some("x".into())),
        },
    );
    emit("evolution-variant-old", Choice::B(Nat::from(128u32)));
    emit("evolution-variant-new", MoreChoice::B(Nat::from(128u32)));
    emit("evolution-variant-used-new", MoreChoice::C("new".into()));
    emit("evolution-array-old", vec![old("abc"), old("xyz")]);
    emit(
        "evolution-array-new",
        vec![new("abc", None), new("xyz", None)],
    );
    emit(
        "evolution-array-mixed",
        vec![new("abc", None), new("xyz", Some("present"))],
    );
    emit(
        "evolution-array-mixed-newer",
        vec![newer("abc", None), newer("xyz", Some("present"))],
    );
    emit(
        "evolution-variant-array-old",
        vec![Choice::A, Choice::B(Nat::from(5u32))],
    );
    emit(
        "evolution-variant-array-new",
        vec![MoreChoice::A, MoreChoice::B(Nat::from(5u32))],
    );
    emit(
        "evolution-outer-old",
        OuterOld {
            inner: old("abc"),
            items: vec![Choice::B(Nat::from(7u32))],
        },
    );
    emit(
        "evolution-outer-new",
        OuterNew {
            inner: new("abc", None),
            items: vec![MoreChoice::B(Nat::from(7u32))],
            extra: None,
        },
    );
    emit("evolution-empty-nat-vector", Vec::<Nat>::new());
    emit("evolution-empty-text-vector", Vec::<String>::new());
    emit("evolution-empty-byte-vector", Vec::<u8>::new());
    emit("evolution-option-none-nat", None::<Nat>);
    emit("evolution-option-none-text", None::<String>);
    emit("evolution-option-some-none", Some(None::<Nat>));
    emit("evolution-option-some-some", Some(Some(Nat::from(7u32))));
    emit(
        "evolution-nullable-array",
        vec![None, Some(None), Some(Some(Nat::from(7u32)))],
    );
}
