import socket
import struct

# --- AYARLAR ---
IP = "127.0.0.1"
PORT = 5001
BUFFER_SIZE = 1024

# --- SAHTE VERİLER ---
# Sanki sunucuda "odev.txt" diye bir dosya varmış gibi davranacağız.
FAKE_FILE_ID = 1
FAKE_FILENAME = b"odev.txt"
FAKE_DATA = b"A" * 5000  # 5000 tane 'A' harfinden oluşan bir dosya
FAKE_FILE_SIZE = len(FAKE_DATA)

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind((IP, PORT))

print(f"TEST SUNUCUSU ÇALIŞIYOR: {IP}:{PORT}")

while True:
    data, addr = sock.recvfrom(BUFFER_SIZE)
    
    # Gelen paketin ilk 10 byte'ını (Header) oku
    if len(data) < 10: continue
    
    req_type, file_id, start_byte, end_byte = struct.unpack('!BBII', data[:10])
    print(f"İstek geldi -> Type: {req_type} | Kimden: {addr}")

    response = b""
    
    # TYPE 1: Dosya Listesi İsteği
    if req_type == 1:
        # Header: Type=1, NumFiles=1, 0, 0
        header = struct.pack('!BBII', 1, 1, 0, 0)
        # Payload: FileID(1 byte) + Filename + Null(0)
        payload = struct.pack('!B', FAKE_FILE_ID) + FAKE_FILENAME + b'\x00'
        response = header + payload

    # TYPE 2: Dosya Boyutu İsteği
    elif req_type == 2:
        if file_id == FAKE_FILE_ID:
            # Header: Type=2, FileID, 0, 0
            header = struct.pack('!BBII', 2, file_id, 0, 0)
            # Payload: Size (4 byte int)
            payload = struct.pack('!I', FAKE_FILE_SIZE)
            response = header + payload

    # TYPE 3: Dosya Parçası (Chunk) İsteği
    elif req_type == 3:
        if file_id == FAKE_FILE_ID:
            # İstenen aralığı kesip alıyoruz
            chunk = FAKE_DATA[start_byte : end_byte+1]
            
            # Header: Type=3, FileID, Start, End
            header = struct.pack('!BBII', 3, file_id, start_byte, end_byte)
            response = header + chunk

    # Cevabı gönder
    if response:
        sock.sendto(response, addr)