//! Windows Job Object RAII wrapper. Closing the last handle kills bound processes.
use std::ffi::c_void;

#[cfg(windows)]
mod platform {
    use super::*;
    use std::io;
    type Handle = *mut c_void;
    #[repr(C)]
    #[derive(Default)]
    struct BasicLimits {
        process_time: i64,
        job_time: i64,
        flags: u32,
        min_working_set: usize,
        max_working_set: usize,
        active_processes: u32,
        affinity: usize,
        priority: u32,
        scheduling: u32,
    }
    #[repr(C)]
    #[derive(Default)]
    struct IoCounters {
        read_ops: u64,
        write_ops: u64,
        other_ops: u64,
        read_bytes: u64,
        write_bytes: u64,
        other_bytes: u64,
    }
    #[repr(C)]
    #[derive(Default)]
    struct ExtendedLimits {
        basic: BasicLimits,
        io: IoCounters,
        process_memory: usize,
        job_memory: usize,
        peak_process_memory: usize,
        peak_job_memory: usize,
    }
    #[link(name = "kernel32")]
    extern "system" {
        fn CreateJobObjectW(attributes: *const c_void, name: *const u16) -> Handle;
        fn SetInformationJobObject(job: Handle, class: i32, info: *const c_void, len: u32) -> i32;
        fn AssignProcessToJobObject(job: Handle, process: Handle) -> i32;
        fn CloseHandle(handle: Handle) -> i32;
    }
    pub struct Job {
        handle: Handle,
    }
    impl Job {
        pub fn create() -> io::Result<Self> {
            let handle = unsafe { CreateJobObjectW(std::ptr::null(), std::ptr::null()) };
            if handle.is_null() {
                return Err(io::Error::last_os_error());
            }
            let job = Self { handle };
            let mut limits = ExtendedLimits::default();
            limits.basic.flags = 0x2000; // JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if unsafe {
                SetInformationJobObject(
                    handle,
                    9,
                    (&limits as *const ExtendedLimits).cast(),
                    std::mem::size_of::<ExtendedLimits>() as u32,
                )
            } == 0
            {
                return Err(io::Error::last_os_error());
            }
            Ok(job)
        }
        /// # Safety
        /// `process` must be a live Windows process handle with assignment rights.
        pub unsafe fn bind(&self, process: Handle) -> io::Result<()> {
            if unsafe { AssignProcessToJobObject(self.handle, process) } == 0 {
                Err(io::Error::last_os_error())
            } else {
                Ok(())
            }
        }
    }
    impl Drop for Job {
        fn drop(&mut self) {
            unsafe {
                CloseHandle(self.handle);
            }
        }
    }
}

#[cfg(windows)]
pub use platform::Job;
#[cfg(not(windows))]
pub struct Job;

/// Returns null on creation failure (or unsupported platforms).
#[no_mangle]
pub extern "C" fn rds_bus_job_create() -> *mut Job {
    #[cfg(windows)]
    {
        Job::create().map_or(std::ptr::null_mut(), |job| Box::into_raw(Box::new(job)))
    }
    #[cfg(not(windows))]
    {
        std::ptr::null_mut()
    }
}

/// Returns zero on success, a positive Windows error code, or INVALID.
/// # Safety
/// `job` and `process` must be live handles; destruction cannot overlap this call.
#[no_mangle]
pub unsafe extern "C" fn rds_bus_job_bind(job: *const Job, process: *mut c_void) -> i32 {
    if job.is_null() || process.is_null() {
        return crate::INVALID;
    }
    #[cfg(windows)]
    {
        unsafe { (&*job).bind(process) }.map_or_else(
            |e| e.raw_os_error().unwrap_or(crate::INVALID),
            |_| crate::OK,
        )
    }
    #[cfg(not(windows))]
    {
        crate::INVALID
    }
}

/// # Safety
/// `job` must be caller-owned and destroyed exactly once. Bound processes die.
#[no_mangle]
pub unsafe extern "C" fn rds_bus_job_destroy(job: *mut Job) {
    if !job.is_null() {
        drop(unsafe { Box::from_raw(job) });
    }
}

#[cfg(all(test, windows))]
mod tests {
    use super::*;
    use std::os::windows::io::AsRawHandle;
    #[test]
    fn create_bind_and_kill_child() {
        let job = Job::create().unwrap();
        let mut child = std::process::Command::new("cmd.exe")
            .args(["/D", "/C", "ping -n 60 127.0.0.1 >NUL"])
            .spawn()
            .unwrap();
        let assigned = unsafe { job.bind(child.as_raw_handle()) };
        if let Err(error) = assigned {
            let _ = child.kill();
            let _ = child.wait();
            panic!("bind failed: {error}");
        }
        assert!(child.try_wait().unwrap().is_none());
        drop(job);
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(5);
        loop {
            if child.try_wait().unwrap().is_some() {
                break;
            }
            if std::time::Instant::now() > deadline {
                let _ = child.kill();
                let _ = child.wait();
                panic!("job did not kill child");
            }
            std::thread::sleep(std::time::Duration::from_millis(10));
        }
    }
}
