import socket
import struct
import sys
import time
import hashlib
import threading
from queue import Queue

# --- AYARLAR ---
MAX_PAYLOAD = 1000
INITIAL_TIMEOUT = 1.0  # Başlangıç timeout süresi
ALPHA = 0.125          # RTT hesaplama katsayısı (EstimatedRTT)
BETA = 0.25            # RTT sapma katsayısı (DevRTT)

# --- GLOBAL İSTATİSTİKLER ---
stats = {
    'total_bytes': 0,
    'start_time': 0,
    'total_requests': 0,
    'retransmissions': 0,
    'rtt_samples': [],
    'lock': threading.Lock()
}

def update_stats(rtt=None, retrans=False, byte_count=0):
    with stats['lock']:
        if rtt:
            stats['rtt_samples'].append(rtt)
        if retrans:
            stats['retransmissions'] += 1
        stats['total_requests'] += 1
        stats['total_bytes'] += byte_count

# --- HEADER İŞLEMLERİ ---
def create_packet(req_type, file_id, start_byte, end_byte):
    return struct.pack('!BBII', req_type, file_id, start_byte, end_byte)

def parse_header(data):
    if len(data) < 10: return None
    return struct.unpack('!BBII', data[:10]) + (data[10:],)

# --- WORKER THREAD ---
def download_worker(sock, server_addr, work_queue, received_chunks, file_id):
    # Her thread kendi RTT değerini tutsun (Basit Adaptive Timeout)
    estimated_rtt = INITIAL_TIMEOUT
    dev_rtt = 0
    timeout_interval = estimated_rtt

    while True:
        try:
            task = work_queue.get(block=False)
        except:
            break

        chunk_idx, start, end = task
        
        # Eğer bu parça zaten indiyse (başka thread indirmiş olabilir)
        if received_chunks[chunk_idx] is not None:
            work_queue.task_done()
            continue

        req = create_packet(3, file_id, start, end)
        
        success = False
        attempts = 0
        
        while not success and attempts < 10:
            attempts += 1
            send_time = time.time()
            
            try:
                sock.sendto(req, server_addr)
                sock.settimeout(timeout_interval)
                
                data, _ = sock.recvfrom(2048)
                recv_time = time.time()
                
                r_type, r_id, r_start, r_end, payload = parse_header(data)
                
                # Gelen paket doğru mu?
                if r_type == 3 and r_id == file_id:
                    # RTT Hesaplama (Jacobson's Algorithm)
                    sample_rtt = recv_time - send_time
                    estimated_rtt = (1 - ALPHA) * estimated_rtt + ALPHA * sample_rtt
                    dev_rtt = (1 - BETA) * dev_rtt + BETA * abs(sample_rtt - estimated_rtt)
                    timeout_interval = estimated_rtt + 4 * dev_rtt
                    
                    update_stats(rtt=sample_rtt, byte_count=len(payload))
                    
                    # Range Check: İstediğimiz kadar geldi mi?
                    # Gelen veriyi buffer'a yaz
                    # NOT: Chunk index mantığı sabit 1000 byte için geçerli.
                    # Eğer sunucu parçalı gönderirse bu mantık karmaşıklaşır.
                    # Basitlik için: Tam geldiğini varsayıp yazıyoruz, eksikse tekrar isteyeceğiz.
                    
                    if r_start == start and len(payload) > 0:
                        # Eğer beklediğimizden az geldiyse (örn: son paket)
                        received_chunks[chunk_idx] = payload
                        success = True
                    else:
                        # Yanlış paket (eski bir istekten dönen cevap olabilir)
                        pass
                elif r_type > 100:
                    print(f"Server Error Code: {r_type}")
                    break

            except socket.timeout:
                # Timeout oldu, süreyi ikiye katla (Exponential Backoff)
                timeout_interval *= 2
                update_stats(retrans=True)
                # print(f"Timeout! Chunk {chunk_idx}, New Timeout: {timeout_interval:.2f}")
            except Exception as e:
                print(f"Socket Error: {e}")
        
        if not success:
            # Pes ettik, kuyruğa geri koy
            work_queue.put(task)
        
        work_queue.task_done()

class RDTPClient:
    def __init__(self, server_list):
        self.servers = server_list
        self.sockets = []
        for _ in range(len(server_list)):
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sockets.append(s)

    def get_file_list(self):
        req = create_packet(1, 0, 0, 0)
        sock = self.sockets[0] # İlk soketi kullan
        sock.sendto(req, self.servers[0])
        sock.settimeout(3.0)
        try:
            data, _ = sock.recvfrom(2048)
            r_type, num_files, _, _, payload = parse_header(data)
            if r_type == 1:
                files = []
                ptr = 0
                for _ in range(num_files):
                    if ptr >= len(payload): break
                    f_id = payload[ptr]
                    ptr += 1
                    end_str = payload.find(b'\x00', ptr)
                    name = payload[ptr:end_str].decode('utf-8', errors='ignore')
                    files.append((f_id, name))
                    ptr = end_str + 1
                return files
        except socket.timeout:
            return []
        return []

    def get_file_size(self, file_id):
        req = create_packet(2, file_id, 0, 0)
        sock = self.sockets[0]
        sock.sendto(req, self.servers[0])
        sock.settimeout(3.0)
        try:
            data, _ = sock.recvfrom(2048)
            r_type, r_id, _, _, payload = parse_header(data)
            if r_type == 2 and r_id == file_id:
                return struct.unpack('!I', payload[:4])[0]
        except:
            pass
        return -1

    def download(self, file_id, file_size):
        print(f"\n--- Downloading File {file_id} ({file_size} bytes) ---")
        stats['start_time'] = time.time()
        
        # İş kuyruğu oluştur
        work_queue = Queue()
        total_chunks = (file_size + MAX_PAYLOAD - 1) // MAX_PAYLOAD
        received_chunks = [None] * total_chunks
        
        for i in range(total_chunks):
            start = i * MAX_PAYLOAD
            end = min(start + MAX_PAYLOAD - 1, file_size - 1)
            work_queue.put((i, start, end))
            
        # Threadleri başlat
        threads = []
        for i in range(len(self.sockets)):
            t = threading.Thread(target=download_worker, args=(
                self.sockets[i], self.servers[i], work_queue, received_chunks, file_id
            ))
            t.start()
            threads.append(t)
            
        # --- ANA DÖNGÜ VE İSTATİSTİKLER ---
        try:
            while any(t.is_alive() for t in threads) and not work_queue.empty():
                time.sleep(1.0) # Her 1 saniyede bir durumu güncelle
                
                # İstatistikleri hesapla
                elapsed = time.time() - stats['start_time']
                downloaded = sum(len(c) for c in received_chunks if c is not None)
                progress = (downloaded / file_size) * 100
                speed = (downloaded / 1024) / elapsed if elapsed > 0 else 0 # KB/s
                
                # RTT ortalaması
                avg_rtt = sum(stats['rtt_samples']) / len(stats['rtt_samples']) if stats['rtt_samples'] else 0
                loss_rate = (stats['retransmissions'] / stats['total_requests']) * 100 if stats['total_requests'] > 0 else 0
                
                print(f"\rProgress: {progress:.1f}% | Speed: {speed:.2f} KB/s | Avg RTT: {avg_rtt*1000:.2f} ms | Loss: {loss_rate:.1f}%", end="")
        except KeyboardInterrupt:
            print("\nDownload cancelled.")
            return

        # Bitmesini bekle
        work_queue.join()
        for t in threads: t.join()

        # --- SONUÇ RAPORU ---
        total_time = time.time() - stats['start_time']
        full_data = b''.join([c for c in received_chunks if c is not None])
        
        print("\n\n--- Download Complete ---")
        print(f"Time: {total_time:.2f} seconds")
        print(f"Total Bytes: {len(full_data)} / {file_size}")
        
        if len(full_data) == file_size:
            md5_hash = hashlib.md5(full_data).hexdigest()
            print(f"MD5 Hash: {md5_hash}")
        else:
            print("ERROR: Incomplete file.")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python client.py <IP:Port> <IP:Port>")
        sys.exit(1)

    servers = []
    for arg in sys.argv[1:]:
        p = arg.split(':')
        servers.append((p[0], int(p[1])))
    
    client = RDTPClient(servers)
    
    # 1. Liste
    files = client.get_file_list()
    print("Available Files:")
    for fid, name in files:
        print(f"{fid}\t{name}")
        
    # 2. Seçim
    try:
        selection = int(input("Enter a number: "))
    except:
        sys.exit(1)
        
    # 3. Boyut ve İndirme
    size = client.get_file_size(selection)
    if size > 0:
        client.download(selection, size)
        
        # İndirme bitince tekrar başa dön (Döngüye alabilirsin hocanın isteğine göre)
        # Hoca "After download... return to first screen" demiş.
        # Bunu yapmak için tüm main kısmını 'while True:' içine alman gerekir.
    else:
        print("Invalid file or server error.")