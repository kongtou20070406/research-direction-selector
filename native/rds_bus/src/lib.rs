//! Bounded FIFO with explicit packet ownership across the C ABI.
//! Foreign callers must supply valid pointers and serialize destruction.
#![deny(unsafe_op_in_unsafe_fn)]

use std::sync::Mutex;

pub const MAX_PAYLOAD: usize = 1024 * 1024;
pub const MAX_CAPACITY: usize = 4096;
pub const MAX_BYTES: usize = 64 * 1024 * 1024;
pub const OK: i32 = 0;
pub const INVALID: i32 = -1;
pub const FULL: i32 = -2;
pub const EMPTY: i32 = -3;
pub const POISONED: i32 = -4;

pub struct Packet {
    kind: u32,
    data: Box<[u8]>,
}

impl Packet {
    pub fn new(kind: u32, len: usize) -> Option<Self> {
        (len <= MAX_PAYLOAD).then(|| Self {
            kind,
            data: vec![0; len].into_boxed_slice(),
        })
    }
    pub fn kind(&self) -> u32 {
        self.kind
    }
    pub fn data(&self) -> &[u8] {
        &self.data
    }
    pub fn data_mut(&mut self) -> &mut [u8] {
        &mut self.data
    }
}

struct Ring {
    slots: Box<[Option<Box<Packet>>]>,
    head: usize,
    len: usize,
    bytes: usize,
    max_bytes: usize,
}

pub struct Bus {
    ring: Mutex<Ring>,
}

impl Bus {
    pub fn new(capacity: usize) -> Option<Self> {
        Self::bounded(capacity, MAX_BYTES)
    }
    pub fn bounded(capacity: usize, max_bytes: usize) -> Option<Self> {
        if capacity == 0 || capacity > MAX_CAPACITY || max_bytes == 0 || max_bytes > MAX_BYTES {
            return None;
        }
        Some(Self {
            ring: Mutex::new(Ring {
                slots: (0..capacity).map(|_| None).collect(),
                head: 0,
                len: 0,
                bytes: 0,
                max_bytes,
            }),
        })
    }
    pub fn post(&self, packet: Box<Packet>) -> Result<(), (i32, Box<Packet>)> {
        let mut ring = match self.ring.lock() {
            Ok(ring) => ring,
            Err(_) => return Err((POISONED, packet)),
        };
        if ring.len == ring.slots.len() || packet.data.len() > ring.max_bytes - ring.bytes {
            return Err((FULL, packet));
        }
        let tail = (ring.head + ring.len) % ring.slots.len();
        ring.bytes += packet.data.len();
        ring.slots[tail] = Some(packet);
        ring.len += 1;
        Ok(())
    }
    pub fn poll(&self) -> Result<Box<Packet>, i32> {
        let mut ring = self.ring.lock().map_err(|_| POISONED)?;
        if ring.len == 0 {
            return Err(EMPTY);
        }
        let head = ring.head;
        let packet = ring.slots[head].take().expect("occupied ring slot");
        ring.head = (head + 1) % ring.slots.len();
        ring.len -= 1;
        ring.bytes -= packet.data.len();
        Ok(packet)
    }
}

#[no_mangle]
pub extern "C" fn rds_bus_version() -> *const std::ffi::c_char {
    c"0.2.0".as_ptr()
}

#[no_mangle]
pub extern "C" fn rds_bus_init(capacity: usize) -> *mut Bus {
    Bus::new(capacity).map_or(std::ptr::null_mut(), |bus| Box::into_raw(Box::new(bus)))
}


#[no_mangle]
pub extern "C" fn rds_bus_abi_version() -> u32 { 2 }
#[no_mangle]
pub extern "C" fn rds_bus_init_bounded(capacity: usize, max_bytes: usize) -> *mut Bus {
    Bus::bounded(capacity, max_bytes).map_or(std::ptr::null_mut(), |b| Box::into_raw(Box::new(b)))
}

/// # Safety
/// `bus` must be a live handle; no operations may overlap its destruction.
#[no_mangle]
pub unsafe extern "C" fn rds_bus_destroy(bus: *mut Bus) {
    if !bus.is_null() {
        drop(unsafe { Box::from_raw(bus) });
    }
}

#[no_mangle]
pub extern "C" fn rds_bus_packet_new(kind: u32, len: usize) -> *mut Packet {
    Packet::new(kind, len).map_or(std::ptr::null_mut(), |p| Box::into_raw(Box::new(p)))
}

/// # Safety
/// `packet` must be owned by the caller, live, and accessed exclusively.
#[no_mangle]
pub unsafe extern "C" fn rds_bus_packet_data(packet: *mut Packet) -> *mut u8 {
    unsafe { packet.as_mut() }.map_or(std::ptr::null_mut(), |p| p.data.as_mut_ptr())
}

/// # Safety
/// `packet` must be live and remain alive during this call.
#[no_mangle]
pub unsafe extern "C" fn rds_bus_packet_len(packet: *const Packet) -> usize {
    unsafe { packet.as_ref() }.map_or(0, |p| p.data.len())
}

/// # Safety
/// `packet` must be live and remain alive during this call.
#[no_mangle]
pub unsafe extern "C" fn rds_bus_packet_kind(packet: *const Packet) -> u32 {
    unsafe { packet.as_ref() }.map_or(0, |p| p.kind)
}

/// # Safety
/// Free only caller-owned packets, exactly once, after releasing all data views.
#[no_mangle]
pub unsafe extern "C" fn rds_bus_packet_free(packet: *mut Packet) {
    if !packet.is_null() {
        drop(unsafe { Box::from_raw(packet) });
    }
}

/// Transfers ownership only on OK. On failure the caller retains the packet.
/// # Safety
/// Both handles must be live; packet must be exclusively owned by the caller.
#[no_mangle]
pub unsafe extern "C" fn rds_bus_post_packet(bus: *const Bus, packet: *mut Packet) -> i32 {
    let Some(bus) = (unsafe { bus.as_ref() }) else {
        return INVALID;
    };
    if packet.is_null() {
        return INVALID;
    }
    match bus.post(unsafe { Box::from_raw(packet) }) {
        Ok(()) => OK,
        Err((code, packet)) => {
            let _ = Box::into_raw(packet);
            code
        }
    }
}

/// Copies bytes once into an owned packet; a null pointer is valid for length zero.
/// # Safety
/// `bus` must be live; `data` must reference `len` readable bytes for this call.
#[no_mangle]
pub unsafe extern "C" fn rds_bus_post_event(
    bus: *const Bus,
    kind: u32,
    data: *const u8,
    len: usize,
) -> i32 {
    let Some(bus) = (unsafe { bus.as_ref() }) else {
        return INVALID;
    };
    if len > MAX_PAYLOAD || (len != 0 && data.is_null()) {
        return INVALID;
    }
    let mut packet = Packet::new(kind, len).expect("validated length");
    if len != 0 {
        packet
            .data
            .copy_from_slice(unsafe { std::slice::from_raw_parts(data, len) });
    }
    bus.post(Box::new(packet))
        .map_or_else(|(code, _)| code, |_| OK)
}

/// Returns caller-owned packet in `out`; sets it to null on failure.
/// # Safety
/// `bus` must be live; `out` must be a valid writable pointer slot.
#[no_mangle]
pub unsafe extern "C" fn rds_bus_poll_event(bus: *const Bus, out: *mut *mut Packet) -> i32 {
    if out.is_null() {
        return INVALID;
    }
    unsafe {
        *out = std::ptr::null_mut();
    }
    let Some(bus) = (unsafe { bus.as_ref() }) else {
        return INVALID;
    };
    match bus.poll() {
        Ok(packet) => {
            unsafe {
                *out = Box::into_raw(packet);
            }
            OK
        }
        Err(code) => code,
    }
}

pub mod job;
pub mod graph;

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn byte_budget_retains_rejected_packet() {
        let bus = Bus::bounded(4, 3).unwrap();
        assert!(bus.post(Box::new(Packet::new(1, 3).unwrap())).is_ok());
        let (code, packet) = bus.post(Box::new(Packet::new(2, 1).unwrap())).unwrap_err();
        assert_eq!(code, FULL);
        assert_eq!(packet.kind(), 2);
        bus.poll().unwrap();
        assert!(bus.post(packet).is_ok());
    }
    #[test]
    fn limits() {
        assert!(Bus::new(0).is_none());
        assert!(Bus::new(MAX_CAPACITY + 1).is_none());
        assert!(Packet::new(1, MAX_PAYLOAD + 1).is_none());
    }
    #[test]
    fn fifo_wrap_and_backpressure() {
        let bus = Bus::new(2).unwrap();
        for kind in 0..2 {
            assert!(bus.post(Box::new(Packet::new(kind, 0).unwrap())).is_ok());
        }
        assert_eq!(
            bus.post(Box::new(Packet::new(9, 0).unwrap()))
                .unwrap_err()
                .0,
            FULL
        );
        assert_eq!(bus.poll().unwrap().kind(), 0);
        assert!(bus.post(Box::new(Packet::new(2, 0).unwrap())).is_ok());
        assert_eq!(bus.poll().unwrap().kind(), 1);
        assert_eq!(bus.poll().unwrap().kind(), 2);
        assert_eq!(bus.poll().err(), Some(EMPTY));
    }
    #[test]
    fn ffi_ownership_and_zero_copy() {
        unsafe {
            let bus = rds_bus_init(1);
            let packet = rds_bus_packet_new(42, 3);
            let data = rds_bus_packet_data(packet);
            std::ptr::copy_nonoverlapping(b"abc".as_ptr(), data, 3);
            assert_eq!(rds_bus_post_packet(bus, packet), OK);
            let rejected = rds_bus_packet_new(2, 0);
            assert_eq!(rds_bus_post_packet(bus, rejected), FULL);
            rds_bus_packet_free(rejected);
            let mut out = std::ptr::null_mut();
            assert_eq!(rds_bus_poll_event(bus, &mut out), OK);
            assert_eq!(out, packet);
            assert_eq!(rds_bus_packet_data(out), data);
            assert_eq!((*out).data(), b"abc");
            assert_eq!(rds_bus_packet_kind(out), 42);
            assert_eq!(rds_bus_packet_len(out), 3);
            rds_bus_packet_free(out);
            assert_eq!(rds_bus_poll_event(bus, &mut out), EMPTY);
            assert!(out.is_null());
            assert_eq!(rds_bus_post_event(bus, 1, std::ptr::null(), 1), INVALID);
            assert_eq!(rds_bus_post_event(bus, 1, std::ptr::null(), 0), OK);
            rds_bus_destroy(bus);
        }
    }
    #[test]
    fn concurrent_producers() {
        let bus = std::sync::Arc::new(Bus::new(400).unwrap());
        let threads: Vec<_> = (0..4)
            .map(|kind| {
                let bus = bus.clone();
                std::thread::spawn(move || {
                    for _ in 0..100 {
                        assert!(bus.post(Box::new(Packet::new(kind, 0).unwrap())).is_ok());
                    }
                })
            })
            .collect();
        for thread in threads {
            thread.join().unwrap();
        }
        let mut counts = [0; 4];
        for _ in 0..400 {
            counts[bus.poll().unwrap().kind() as usize] += 1;
        }
        assert_eq!(counts, [100; 4]);
    }
}
