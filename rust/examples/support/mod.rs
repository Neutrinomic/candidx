use candid::{CandidType, Deserialize, Nat, Principal};

#[derive(CandidType, Deserialize, Clone, PartialEq, Debug)]
pub struct Tx {
    pub owner: Principal,
    pub amount: Nat,
    pub memo: Option<Vec<u8>>,
}
#[derive(CandidType, Deserialize, Clone, PartialEq, Debug)]
#[allow(non_snake_case)]
pub struct Block {
    pub timestamp: u64,
    pub parentHash: Option<Vec<u8>>,
    pub tx: Vec<Tx>,
    pub note: Option<String>,
}
pub const WORKLOADS: [(&str, usize, usize); 4] = [
    ("small", 4, 32),
    ("records-128", 128, 32),
    ("blob-64k", 1, 65536),
    ("records-1024", 1024, 0),
];
pub fn block(count: usize, memo_size: usize) -> Block {
    let memo: Vec<u8> = (0..memo_size).map(|i| (i % 256) as u8).collect();
    let huge =
        Nat::parse(b"1606938044258990275541962092341162602522202993782792835301376").unwrap();
    Block {
        timestamp: 1234567890,
        parentHash: Some(vec![7; 32]),
        note: None,
        tx: (0..count)
            .map(|i| Tx {
                owner: Principal::from_slice(&[1, 2]),
                amount: Nat(&huge.0 + i as u32),
                memo: if i % 3 == 0 || memo_size == 65536 {
                    Some(memo.clone())
                } else {
                    None
                },
            })
            .collect(),
    }
}
