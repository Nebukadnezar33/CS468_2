# CS468_2
# Reliable Data Transfer Protocol (RDTP) Client
CS468 Project 2

## Team Members
* Barış KARABOĞA - S034149
* Vahit Eren PINAR - S0

## Project Description
This project implements a reliable file transfer client protocol over UDP. The client is designed to download files from a server that simulates unreliable network conditions such as packet loss, delay, and bandwidth throttling. The implementation focuses on reliability, congestion control, and maximizing throughput using multiple network interfaces simultaneously.

## Key Features
* **Multi-Interface Support (Multi-Homing):** The client creates multiple threads to download data chunks in parallel through different network interfaces (or ports), effectively balancing the load and increasing download speed.
* **Reliability over UDP:** Implements a custom retransmission mechanism. It detects lost packets (timeouts) and re-requests them until the data is successfully received.
* **Adaptive Timeout:** Instead of a static timeout value, the client dynamically calculates the Round Trip Time (RTT) using **Jacobson/Karels algorithm**. This allows the protocol to adapt to varying network conditions (congestion or high latency).
* **Data Integrity:** Verifies the downloaded file using **MD5 checksum** validation to ensure the file is identical to the source.
* **Real-time Statistics:** Displays detailed progress information including transfer speed (KB/s), average RTT, packet loss rate, and download percentage.

## Requirements
* Python 3.x
* No external libraries required (uses standard `socket`, `threading`, `struct`, `time`, `hashlib`).

## How to Run

Run the `client.py` script by providing the Server IP and Port pairs as command-line arguments. You can provide multiple address pairs to utilize multiple interfaces.

### Usage Syntax
```bash
python client.py <Server_IP1>:<Port1> <Server_IP2>:<Port2>
