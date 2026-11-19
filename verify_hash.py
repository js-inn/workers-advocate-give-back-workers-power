import hashlib
import sys

def calculate_block_hash(header_hex: str) -> str:
    try:
        header_bytes = bytes.fromhex(header_hex)
    except ValueError as e:
        print(f"Error: Invalid hex string provided -> {e}")
        sys.exit(1)
        
    # Perform double SHA-256 (SHA-256d)
    first_hash = hashlib.sha256(header_bytes).digest()
    second_hash = hashlib.sha256(first_hash).digest()
    
    # Return in reverse-byte (little-endian) format commonly used in blockchain ledgers
    return second_hash[::-1].hex()

def verify_proof_of_work(block_hash_hex: str, target_hex: str) -> bool:
    hash_int = int(block_hash_hex, 16)
    target_int = int(target_hex, 16)
    return hash_int <= target_int

if __name__ == "__main__":
    # Example sample header (80 bytes in hex representation)
    sample_header = "0100000081cd02ab7e569e8bcd9317e2fe99f2de44d49ab2b8851ba4a308000000000000e320b6c2fffc8d750423db8b1eb942ae710e951ed797f7affc8892b0f1fc122b519104541a32154c29babe27"
    
    computed_hash = calculate_block_hash(sample_header)
    print(f"Computed Hash: {computed_hash}")
    
    # Test difficulty target validation
    sample_target = "00000000000000000001ffffffffffffffffffffffffffffffffffffffffffff"
    is_valid = verify_proof_of_work(computed_hash, sample_target)
    print(f"Meets difficulty target: {is_valid}")

