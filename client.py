import socket
import struct
import sys
import time
import hashlib
import threading
from queue import Queue

# --- AYARLAR ---
MAX_PAYLOAD = 1000  # Dokümanda belirtilen max veri boyutu
TIMEOUT = 2.0       # Saniye cinsinden timeout (bunu dinamik yapabilirsin)
BUFFER_SIZE = 2048  # Gelen paket için buffer

# --- PAKET YARDIMCILARI ---
def create_packet(req_type, file_id, start_byte, end_byte):
    # !BBII formatı: 1 byte, 1 byte, 4 byte, 4 byte (Big Endian)
    header = struct.pack('!BBII', req_type, file_id, start_byte, end_byte)
    return header

def parse_header(data):
    # İlk 10 byte header
    if len(data) < 10:
        return None
    header = data[:10]
    # Unpack returns a tuple
    resp_type, file_id, start_byte, end_byte = struct.unpack('!BBII', header)
    payload = data[10:]
    return resp_type, file_id, start_byte, end_byte, payload

# --- DOSYA İNDİRME WORKER (THREAD) ---
def download_worker(sock, server_addr, work_queue, received_chunks, file_id):
    while True:
        try:
            # Kuyruktan iş al: (chunk_index, start_byte, end_byte)
            task = work_queue.get(block=False)
        except:
            break # Kuyruk boşsa çık

        chunk_idx, start, end = task
        
        # Eğer bu parça zaten indiyse geç (Duplicate ACK önlemek için basit kontrol)
        if received_chunks[chunk_idx] is not None:
            work_queue.task_done()
            continue

        req = create_packet(3, file_id, start, end) # Type 3: GetFileData

        # Basit bir Stop-and-Wait / Retry mantığı
        attempts = 0
        success = False
        while not success and attempts < 5: # 5 kere dene
            try:
                sock.sendto(req, server_addr)
                sock.settimeout(TIMEOUT) # Timeout ayarla
                
                data, _ = sock.recvfrom(BUFFER_SIZE)
                r_type, r_id, r_start, r_end, payload = parse_header(data)

                # Gelen paket bizim istediğimiz mi?
                if r_type == 3 and r_id == file_id and r_start == start:
                    received_chunks[chunk_idx] = payload
                    success = True
                else:
                    # Yanlış paket veya hata kodu
                    pass
            except socket.timeout:
                # Zaman aşımı, tekrar dene
                attempts += 1
                # print(f"Timeout for chunk {chunk_idx}, retrying...")
            except Exception as e:
                print(f"Socket error: {e}")
                attempts += 1
        
        if not success:
            # Başarısız olduysa kuyruğa geri ekle (veya pes et)
            print(f"Chunk {chunk_idx} failed after retries. Putting back to queue.")
            work_queue.put(task)
        
        work_queue.task_done()

# --- ANA CLIENT SINIFI ---
class RDTPClient:
    def __init__(self, server_list):
        # server_list: [('ip', port), ('ip', port)]
        self.servers = server_list
        self.sockets = []
        for _ in range(len(server_list)):
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sockets.append(s)
        print(f"Client started with {len(self.sockets)} interfaces.")

    def get_file_list(self):
        # Herhangi bir soketten liste iste (Request Type 1)
        req = create_packet(1, 0, 0, 0)
        sock = self.sockets[0]
        server = self.servers[0]
        
        sock.sendto(req, server)
        sock.settimeout(3.0)
        
        try:
            data, _ = sock.recvfrom(BUFFER_SIZE)
            r_type, num_files, _, _, payload = parse_header(data)
            
            if r_type == 1:
                files = []
                # Payload: array of file_descriptor
                # file_id (1 byte) + filename (null terminated string)
                ptr = 0
                for _ in range(num_files):
                    if ptr >= len(payload): break
                    f_id = payload[ptr]
                    ptr += 1
                    # Stringi null karaktere kadar oku
                    end_str = payload.find(b'\x00', ptr)
                    name = payload[ptr:end_str].decode('utf-8')
                    files.append((f_id, name))
                    ptr = end_str + 1
                return files
        except socket.timeout:
            print("Dosya listesi alınamadı (Timeout).")
            return []
        return []

    def get_file_size(self, file_id):
        # Request Type 2
        req = create_packet(2, file_id, 0, 0)
        sock = self.sockets[0]
        server = self.servers[0]
        
        sock.sendto(req, server)
        sock.settimeout(3.0)
        
        try:
            data, _ = sock.recvfrom(BUFFER_SIZE)
            r_type, r_id, _, _, payload = parse_header(data)
            if r_type == 2 and r_id == file_id:
                # Size 4 byte içinde geliyor
                size = struct.unpack('!I', payload[:4])[0]
                return size
        except Exception as e:
            print(f"Size hatası: {e}")
        return -1

    def download(self, file_id, file_size):
        print(f"Downloading File ID: {file_id}, Size: {file_size} bytes")
        start_time = time.time()
        
        # 1. İş Kuyruğunu Oluştur (Chunks)
        work_queue = Queue()
        total_chunks = (file_size + MAX_PAYLOAD - 1) // MAX_PAYLOAD
        
        # Sonuçları tutacak liste (Thread-safe olması için index ile erişeceğiz)
        received_chunks = [None] * total_chunks
        
        for i in range(total_chunks):
            start = i * MAX_PAYLOAD
            end = min(start + MAX_PAYLOAD - 1, file_size - 1) # Range inclusive
            # (Chunk Index, Start, End)
            work_queue.put((i, start, end))
            
        # 2. Threadleri Başlat (Her arayüz/soket için bir thread)
        threads = []
        for i in range(len(self.sockets)):
            t = threading.Thread(target=download_worker, args=(
                self.sockets[i], 
                self.servers[i], 
                work_queue, 
                received_chunks, 
                file_id
            ))
            t.start()
            threads.append(t)
            
        # 3. İlerleme Çubuğu (Opsiyonel Main Loop)
        while any(t.is_alive() for t in threads) and not work_queue.empty():
            time.sleep(0.5)
            done = sum(1 for c in received_chunks if c is not None)
            print(f"Progress: {done}/{total_chunks} chunks ({(done/total_chunks)*100:.1f}%)")

        # Threadlerin bitmesini bekle
        work_queue.join()
        for t in threads:
            t.join() # Workerlarda "while True" queue bitince break ediyor

        duration = time.time() - start_time
        print(f"\nDownload finished in {duration:.2f} seconds.")
        
        # 4. Dosyayı Birleştir ve MD5
        full_data = b''.join([c for c in received_chunks if c is not None])
        
        # Eğer eksik varsa (None varsa) hata
        if len(full_data) != file_size:
            print("HATA: Dosya boyutu uyuşmuyor (Bazı paketler kayıp).")
        else:
            md5_hash = hashlib.md5(full_data).hexdigest()
            print(f"MD5 Checksum: {md5_hash}")

# --- MAIN ---
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python client.py <server_ip1:port1> <server_ip2:port2>")
        # Test için varsayılan değerler (Docker yerel test)
        # sys.argv.append("127.0.0.1:5000")
        # sys.argv.append("127.0.0.1:5001")
        sys.exit(1)

    server_args = sys.argv[1:]
    server_list = []
    for s in server_args:
        parts = s.split(':')
        server_list.append((parts[0], int(parts[1])))

    client = RDTPClient(server_list)

    # 1. Listeyi Al
    files = client.get_file_list()
    print("\nAvailable Files:")
    for f_id, name in files:
        print(f"{f_id}: {name}")

    if not files:
        print("Dosya listesi alınamadı.")
        sys.exit(1)

    # 2. Kullanıcı Seçimi
    choice = int(input("\nEnter file ID to download: "))
    
    # 3. Boyutu Al
    size = client.get_file_size(choice)
    if size > 0:
        # 4. İndir
        client.download(choice, size)
    else:
        print("Dosya boyutu alınamadı.")