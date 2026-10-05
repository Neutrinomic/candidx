use std::io::{self, BufRead};
fn main() {
    for line in io::stdin().lock().lines() {
        let bytes = hex::decode(line.expect("stdin")).expect("hex input");
        match candidx::canonicalize(&bytes) {
            Ok(bytes) => println!("ok {}", hex::encode(bytes)),
            Err(error) => println!("error {error}"),
        }
    }
}
