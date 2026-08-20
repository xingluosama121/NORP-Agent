// Vibe Coding Agent - 视觉/操作外挂：capture_worker（Graphics Capture 单窗口捕获）
// Copyright (c) 2026 xingluosama
//
// 功能：捕获用户显式指定的单个窗口（OBS 式 Graphics Capture，被遮挡/移出屏幕也可见），
//       回读为 CPU 可访问的 BGRA 像素。
//
// 两种运行模式：
//   1. 单帧模式：capture_worker.exe <hwnd> <output.bmp>
//      抓一帧，写 32bpp BMP 文件后退出。
//   2. 驻留模式：capture_worker.exe --serve <hwnd>
//      常驻进程，持续捕获；从 stdin 读命令，按需向 stdout 供帧。
//      命令（每行一条）：
//        shot  → 输出最新一帧：<4 字节小端长度><BMP 数据>（长度 0 表示尚无帧）
//        quit  → 退出
//      供 Python 侧做「动作-验证-收敛」的快速重捕获，避免每次验证都冷启动进程。
//
// 安全设计（见 docs/vision_agent_design.md）：
//   - 不提权：普通用户权限即可运行（Graphics Capture 不需要管理员）。
//   - 只捕获用户显式传入的单个窗口，绝不捕获整个桌面。
//   - 全程 RAII（winrt::com_ptr / cppwinrt 投影），任何一步失败即 fail-fast 返回。
//     进程崩溃可被上层杀掉重启，破坏半径可控。
//
// 编译：capture_worker\build.bat（MSVC + Windows SDK + C++/WinRT，无需 CMake）

#include <windows.h>
#include <d3d11.h>
#include <dxgi1_2.h>

#include <winrt/Windows.Foundation.h>
#include <winrt/Windows.Graphics.Capture.h>
#include <winrt/Windows.Graphics.DirectX.Direct3D11.h>
#include <winrt/Windows.Graphics.DirectX.h>

// interop 头文件（从 HWND 建捕获项 / 从 IDirect3DSurface 拿 IDXGISurface）
#include <windows.graphics.capture.interop.h>
#include <windows.graphics.directx.direct3d11.interop.h>

#include <cstdio>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <io.h>
#include <iostream>
#include <mutex>
#include <string>
#include <vector>

namespace winrt_cap = winrt::Windows::Graphics::Capture;
namespace winrt_dx = winrt::Windows::Graphics::DirectX;
namespace winrt_d3d11 = winrt::Windows::Graphics::DirectX::Direct3D11;
// interop 头文件定义的经典 COM 接口（全局 ::Windows 命名空间，非 winrt::）
namespace dx_interop = ::Windows::Graphics::DirectX::Direct3D11;

using winrt::Windows::Graphics::SizeInt32;

namespace {

// ── 小工具 ──────────────────────────────────────────────────────

bool parse_hwnd(const char* s, HWND& out) {
    if (!s || !*s) return false;
    char* end = nullptr;
    unsigned long long v = std::strtoull(s, &end, 0);
    if (end == s) return false;
    out = reinterpret_cast<HWND>(static_cast<uintptr_t>(v));
    return true;
}

std::string hresult_str(HRESULT hr) {
    char buf[64];
    std::snprintf(buf, sizeof(buf), "0x%08X", static_cast<unsigned>(hr));
    return std::string(buf);
}

void fail(const char* msg) {
    std::fprintf(stderr, "[capture_worker] ERROR: %s\n", msg);
}

// ── 捕获上下文（RAII） ─────────────────────────────────────────

struct CaptureContext {
    winrt::com_ptr<ID3D11Device> device;
    winrt::com_ptr<ID3D11DeviceContext> context;
    winrt_cap::GraphicsCaptureItem item{ nullptr };
    winrt_d3d11::IDirect3DDevice d3d_rt_device{ nullptr };
    winrt_cap::Direct3D11CaptureFramePool frame_pool{ nullptr };
    winrt_cap::GraphicsCaptureSession session{ nullptr };
};

// 初始化捕获：D3D11 设备 → capture item → frame pool → session。
// 成功返回 0，失败返回非 0 错误码（并打印原因）。
int init_capture(HWND hwnd, CaptureContext& ctx) {
    // DPI 感知必须先于一切窗口/DPI 操作。
    SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
    winrt::init_apartment(winrt::apartment_type::multi_threaded);

    if (!hwnd || !IsWindow(hwnd)) {
        fail("target window not found");
        return 3;
    }

    HRESULT hr = D3D11CreateDevice(
        nullptr, D3D_DRIVER_TYPE_HARDWARE, nullptr,
        D3D11_CREATE_DEVICE_BGRA_SUPPORT,
        nullptr, 0, D3D11_SDK_VERSION,
        ctx.device.put(), nullptr, ctx.context.put());
    if (FAILED(hr) || !ctx.device) {
        fail(("D3D11CreateDevice failed: " + hresult_str(hr)).c_str());
        return 4;
    }

    try {
        auto interop_factory =
            winrt::get_activation_factory<winrt_cap::GraphicsCaptureItem, IGraphicsCaptureItemInterop>();
        winrt::check_hresult(interop_factory->CreateForWindow(
            hwnd, winrt::guid_of<winrt_cap::GraphicsCaptureItem>(), winrt::put_abi(ctx.item)));
    } catch (const winrt::hresult_error& e) {
        fail(("CreateForWindow failed: " + hresult_str(e.code())).c_str());
        return 5;
    }
    if (!ctx.item) {
        fail("CreateForWindow returned null item");
        return 5;
    }

    winrt::com_ptr<IDXGIDevice> dxgi_device;
    try {
        ctx.device.as(dxgi_device);
    } catch (const winrt::hresult_error& e) {
        fail(("failed to get IDXGIDevice: " + hresult_str(e.code())).c_str());
        return 6;
    }
    hr = CreateDirect3D11DeviceFromDXGIDevice(
        dxgi_device.get(),
        reinterpret_cast<::IInspectable**>(winrt::put_abi(ctx.d3d_rt_device)));
    if (FAILED(hr) || !ctx.d3d_rt_device) {
        fail(("CreateDirect3D11DeviceFromDXGIDevice failed: " + hresult_str(hr)).c_str());
        return 6;
    }

    SizeInt32 size = ctx.item.Size();
    if (size.Width <= 0 || size.Height <= 0) {
        fail("capture item has invalid size (window minimized?)");
        return 7;
    }

    try {
        ctx.frame_pool = winrt_cap::Direct3D11CaptureFramePool::CreateFreeThreaded(
            ctx.d3d_rt_device,
            winrt_dx::DirectXPixelFormat::B8G8R8A8UIntNormalized,
            2, size);
        ctx.session = ctx.frame_pool.CreateCaptureSession(ctx.item);
    } catch (const winrt::hresult_error& e) {
        fail(("create frame pool / session failed: " + hresult_str(e.code())).c_str());
        return 8;
    }

    return 0;
}

// 回读一帧 → 紧密排列的 BGRA pixels（top-down）。成功返回 true。
// 在回调线程内调用（D3D11 immediate context 非线程安全，不能跨线程混用）。
bool read_frame_to_pixels(
    const winrt_cap::Direct3D11CaptureFrame& frame,
    CaptureContext& ctx,
    std::vector<uint8_t>& pixels,
    uint32_t& out_width,
    uint32_t& out_height) {
    SizeInt32 size = frame.ContentSize();
    if (size.Width <= 0 || size.Height <= 0) return false;

    winrt_d3d11::IDirect3DSurface surface = frame.Surface();
    auto access = surface.as<dx_interop::IDirect3DDxgiInterfaceAccess>();
    winrt::com_ptr<IDXGISurface> dxgi_surface;
    winrt::check_hresult(access->GetInterface(
        winrt::guid_of<IDXGISurface>(), dxgi_surface.put_void()));

    winrt::com_ptr<ID3D11Texture2D> texture;
    dxgi_surface.as(texture);
    if (!texture) return false;

    D3D11_TEXTURE2D_DESC desc = {};
    texture->GetDesc(&desc);
    desc.Usage = D3D11_USAGE_STAGING;
    desc.BindFlags = 0;
    desc.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
    desc.MiscFlags = 0;

    winrt::com_ptr<ID3D11Texture2D> staging;
    winrt::check_hresult(ctx.device->CreateTexture2D(&desc, nullptr, staging.put()));
    ctx.context->CopyResource(staging.get(), texture.get());

    D3D11_MAPPED_SUBRESOURCE mapped{};
    HRESULT hr = ctx.context->Map(staging.get(), 0, D3D11_MAP_READ, 0, &mapped);
    if (FAILED(hr)) return false;

    uint32_t width = static_cast<uint32_t>(size.Width);
    uint32_t height = static_cast<uint32_t>(size.Height);
    uint32_t row_bytes = width * 4;       // BGRA
    uint32_t src_pitch = mapped.RowPitch; // 行距（可能 > row_bytes，含对齐）

    pixels.resize(static_cast<size_t>(height) * row_bytes);
    const uint8_t* src = static_cast<const uint8_t*>(mapped.pData);
    for (uint32_t y = 0; y < height; ++y) {
        std::memcpy(
            pixels.data() + static_cast<size_t>(y) * row_bytes,
            src + static_cast<size_t>(y) * src_pitch,
            row_bytes);
    }
    ctx.context->Unmap(staging.get(), 0);

    out_width = width;
    out_height = height;
    return true;
}

// 紧密排列的 BGRA pixels（top-down）→ 32bpp BMP bytes（bottom-up，最兼容）。
std::vector<uint8_t> encode_bmp(
    const std::vector<uint8_t>& pixels, uint32_t width, uint32_t height) {
    uint32_t row_bytes = width * 4;
    uint32_t data_size = height * row_bytes;
    uint32_t file_size = 54 + data_size;
    std::vector<uint8_t> bmp(file_size, 0);

    auto w16 = [&](size_t off, uint16_t v) { std::memcpy(&bmp[off], &v, 2); };
    auto w32 = [&](size_t off, uint32_t v) { std::memcpy(&bmp[off], &v, 4); };

    w16(0, 0x4D42);       // "BM"
    w32(2, file_size);
    w32(10, 54);          // pixel data offset
    w32(14, 40);          // BITMAPINFOHEADER size
    w32(18, width);
    w32(22, height);      // 正数 = bottom-up
    w16(26, 1);           // planes
    w16(28, 32);          // bpp
    w32(30, 0);           // BI_RGB
    w32(34, data_size);

    // bottom-up：从最后一行写到第一行
    for (uint32_t y = 0; y < height; ++y) {
        std::memcpy(&bmp[54 + static_cast<size_t>(y) * row_bytes],
                    &pixels[static_cast<size_t>(height - 1 - y) * row_bytes],
                    row_bytes);
    }
    return bmp;
}

// ── 单帧模式 ───────────────────────────────────────────────────

int run_single(HWND hwnd, const char* out_path) {
    CaptureContext ctx;
    int rc = init_capture(hwnd, ctx);
    if (rc != 0) return rc;

    HANDLE frame_event = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    if (!frame_event) {
        fail("CreateEventW failed");
        return 9;
    }

    struct CaptureOut {
        std::vector<uint8_t> pixels;
        uint32_t width = 0;
        uint32_t height = 0;
        bool ok = false;
    } out;

    auto token = ctx.frame_pool.FrameArrived(
        [&](winrt_cap::Direct3D11CaptureFramePool const& pool,
            winrt::Windows::Foundation::IInspectable const&) {
            winrt_cap::Direct3D11CaptureFrame frame{ nullptr };
            try {
                frame = pool.TryGetNextFrame();
            } catch (const winrt::hresult_error&) {
                SetEvent(frame_event);
                return;
            }
            if (!frame) {
                SetEvent(frame_event);
                return;
            }
            try {
                out.ok = read_frame_to_pixels(frame, ctx, out.pixels, out.width, out.height);
            } catch (const winrt::hresult_error&) {
                out.ok = false;
            }
            SetEvent(frame_event);
        });

    try {
        ctx.session.StartCapture();
    } catch (const winrt::hresult_error& e) {
        fail(("StartCapture failed: " + hresult_str(e.code())).c_str());
        CloseHandle(frame_event);
        return 10;
    }

    DWORD wait_rc = WaitForSingleObject(frame_event, 5000);
    if (wait_rc != WAIT_OBJECT_0) {
        fail("timeout waiting for first frame");
        try { ctx.session.Close(); } catch (...) {}
        CloseHandle(frame_event);
        return 11;
    }
    if (!out.ok || out.pixels.empty()) {
        fail("no valid frame captured");
        try { ctx.session.Close(); } catch (...) {}
        CloseHandle(frame_event);
        return 11;
    }

    std::vector<uint8_t> bmp = encode_bmp(out.pixels, out.width, out.height);
    FILE* fp = nullptr;
    if (fopen_s(&fp, out_path, "wb") != 0 || !fp) {
        fail(("cannot open output file: " + std::string(out_path)).c_str());
        try { ctx.session.Close(); } catch (...) {}
        CloseHandle(frame_event);
        return 13;
    }
    fwrite(bmp.data(), 1, bmp.size(), fp);
    fclose(fp);

    try { ctx.session.Close(); } catch (...) {}
    ctx.frame_pool.FrameArrived(token);
    CloseHandle(frame_event);

    std::printf("[capture_worker] captured %ux%u -> %s\n", out.width, out.height, out_path);
    return 0;
}

// ── 驻留模式（--serve）─────────────────────────────────────────

int run_serve(HWND hwnd) {
    CaptureContext ctx;
    int rc = init_capture(hwnd, ctx);
    if (rc != 0) return rc;

    // 最新帧（free-threaded 回调写，主循环读，加锁保护）
    std::mutex mtx;
    std::vector<uint8_t> latest_bmp;
    uint32_t latest_width = 0;
    uint32_t latest_height = 0;
    bool has_frame = false;

    HANDLE first_frame_event = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    if (!first_frame_event) {
        fail("CreateEventW failed");
        return 9;
    }

    auto token = ctx.frame_pool.FrameArrived(
        [&](winrt_cap::Direct3D11CaptureFramePool const& pool,
            winrt::Windows::Foundation::IInspectable const&) {
            winrt_cap::Direct3D11CaptureFrame frame{ nullptr };
            try {
                frame = pool.TryGetNextFrame();
            } catch (const winrt::hresult_error&) {
                return;
            }
            if (!frame) return;
            try {
                std::vector<uint8_t> pixels;
                uint32_t w = 0, h = 0;
                if (read_frame_to_pixels(frame, ctx, pixels, w, h)) {
                    std::vector<uint8_t> bmp = encode_bmp(pixels, w, h);
                    std::lock_guard<std::mutex> lock(mtx);
                    latest_bmp = std::move(bmp);
                    latest_width = w;
                    latest_height = h;
                    has_frame = true;
                }
            } catch (const winrt::hresult_error&) {
                // 单帧失败忽略，等待下一帧
            }
            SetEvent(first_frame_event);
        });

    try {
        ctx.session.StartCapture();
    } catch (const winrt::hresult_error& e) {
        fail(("StartCapture failed: " + hresult_str(e.code())).c_str());
        CloseHandle(first_frame_event);
        return 10;
    }

    // 等第一帧就绪，再对外宣布 ready，确保 shot 立即有帧可返回。
    if (WaitForSingleObject(first_frame_event, 5000) != WAIT_OBJECT_0) {
        fail("timeout waiting for first frame");
        try { ctx.session.Close(); } catch (...) {}
        CloseHandle(first_frame_event);
        return 11;
    }
    CloseHandle(first_frame_event);

    std::fprintf(stderr, "[capture_worker] serve mode ready\n");
    std::fflush(stderr);

    std::string line;
    while (std::getline(std::cin, line)) {
        // Windows 管道/echo 的行尾是 \r\n，getline 去掉 \n 但保留 \r，这里手动去 \r
        if (!line.empty() && line.back() == '\r') {
            line.pop_back();
        }
        if (line == "shot") {
            std::vector<uint8_t> bmp;
            {
                std::lock_guard<std::mutex> lock(mtx);
                bmp = latest_bmp;
            }
            uint32_t len = static_cast<uint32_t>(bmp.size());
            // <4 字节小端长度><BMP 数据>
            fwrite(&len, 4, 1, stdout);
            if (len > 0) {
                fwrite(bmp.data(), 1, bmp.size(), stdout);
            }
            fflush(stdout);
        } else if (line == "quit") {
            break;
        }
    }

    try { ctx.session.Close(); } catch (...) {}
    ctx.frame_pool.FrameArrived(token);
    return 0;
}

} // namespace

int main(int argc, char** argv) {
    // stdin/stdout 设为二进制模式：BMP 帧是二进制数据，若走文本模式，
    // 其中的 0x0A 会被 \n→\r\n 转换，导致长度错位、连续取帧卡死。
    _setmode(_fileno(stdin), _O_BINARY);
    _setmode(_fileno(stdout), _O_BINARY);

    if (argc >= 3 && std::strcmp(argv[1], "--serve") == 0) {
        HWND hwnd = nullptr;
        if (!parse_hwnd(argv[2], hwnd)) {
            std::fprintf(stderr, "[capture_worker] ERROR: invalid hwnd '%s'\n", argv[2]);
            return 2;
        }
        return run_serve(hwnd);
    }

    if (argc < 3) {
        std::fprintf(stderr,
                     "usage:\n"
                     "  capture_worker.exe <hwnd> <output.bmp>   (single frame)\n"
                     "  capture_worker.exe --serve <hwnd>        (persistent)\n");
        return 2;
    }

    HWND hwnd = nullptr;
    if (!parse_hwnd(argv[1], hwnd)) {
        std::fprintf(stderr, "[capture_worker] ERROR: invalid hwnd '%s'\n", argv[1]);
        return 2;
    }
    return run_single(hwnd, argv[2]);
}
