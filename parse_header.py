import struct
from datetime import datetime

def parse_block_header(header_hex: str) -> dict:
    """Parse an 80-byte block header hex string into its constituent fields."""
    header_bytes = bytes.fromhex(header_hex)
    
    if len(header_bytes) != 80:
        raise ValueError(f"Block header must be exactly 80 bytes (160 hex chars). Got {len(header_bytes)} bytes.")
    
    # Unpack using struct format string ('<' = little-endian, 'I' = 4-byte uint, '32s' = 32 raw bytes)
    version, prev_hash_bytes, merkle_root_bytes, timestamp, bits, nonce = struct.unpack(
        "<I32s32sIII", header_bytes
    )
    
    # Blockchain hashes are displayed in reverse-byte order (big-endian hex)
    prev_hash = prev_hash_bytes[::-1].hex()
    merkle_root = merkle_root_bytes[::-1].hex()
    
    # Convert timestamp to human-readable UTC string
    readable_time = datetime.utcfromtimestamp(timestamp).isoformat() + "Z"
    
    return {
        "version": version,
        "prev_hash": prev_hash,
        "merkle_root": merkle_root,
        "timestamp": timestamp,
        "utc_time": readable_time,
        "bits": hex(bits),
        "nonce": nonce
    }

if __name__ == "__main__":
    # Example 80-byte block header (160 hex characters)
    sample_header = "0100000081cd02ab7e569e8bcd9317e2fe99f2de44d49ab2b8851ba4a308000000000000e320b6c2fffc8d750423db8b1eb942ae710e951ed797f7affc8892b0f1fc122b519104541a32154c29babe27"
    
    parsed_fields = parse_block_header(sample_header)
    
    print("=== PARSED BLOCK HEADER FIELDS ===")
    for key, value in parsed_fields.items():
        print(f"{key:15}: {value}")

