#pragma once

#include "esphome/core/component.h"
#include "esphome/core/hal.h"
#include "esphome/core/log.h"
#include "esphome/components/usb_host/usb_host.h"
#include "usb/usb_host.h"

#include <atomic>
#include <cstddef>
#include <cstdint>
#include <string>

#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include <freertos/semphr.h>
#include <freertos/task.h>

namespace esphome::dymo_printserver {

// Transparent raw-9100 (JetDirect/AppSocket) -> USB Printer Class bridge for the
// DYMO LabelWriter 450.
//
// The LabelWriter 450 is a standard USB Printer Class (class 0x07) device: the
// client renders the label into DYMO's ESC raster stream and sends it to TCP port
// 9100, and this component forwards those bytes verbatim to the printer's bulk
// OUT endpoint. It does not interpret the raster data, and it never injects PJL
// or any other control stream into the job (the LabelWriter has no PJL
// interpreter, so a page-count query would corrupt the label).
class DymoPrintServer : public usb_host::USBClient {
 public:
  DymoPrintServer(uint16_t vid, uint16_t pid) : usb_host::USBClient(vid, pid) {}

  void setup() override;
  void loop() override;
  void dump_config() override;

  void set_port(uint16_t port) { this->port_ = port; }
  /// Enable/disable the USB Printer Class GET_PORT_STATUS poll (IEEE-1284 byte).
  void set_status_polling_enabled(bool enabled) { this->status_polling_enabled_ = enabled; }

  /// True while the printer is enumerated, claimed, and selected for raw data.
  bool is_ready() const { return this->printer_ready_.load(std::memory_order_acquire); }

  /// Last IEEE-1284 status byte reported by the printer (0 until the first read).
  uint8_t printer_status() const { return this->printer_status_.load(std::memory_order_acquire); }
  /// True once a status byte has been read since the printer last connected.
  bool has_status() const { return this->has_status_.load(std::memory_order_acquire); }
  /// Out of labels (status bit 5 "PaperEmpty").
  bool is_paper_empty() const { return this->is_ready() && (this->printer_status() & STATUS_PAPER_EMPTY) != 0; }
  /// Error / needs attention (status bit 3 "NotError" is clear).
  bool is_error() const { return this->is_ready() && (this->printer_status() & STATUS_NOT_ERROR) == 0; }
  /// Selected / online (status bit 4 "Select").
  bool is_online() const { return this->is_ready() && (this->printer_status() & STATUS_SELECT) != 0; }

  /// Render one line of text into a DYMO ESC/P raster label and submit it to the
  /// local raw-9100 pipeline. The line is drawn in the large DejaVuSans-Bold face
  /// (the font of the sample label) and centred vertically on the label; a line
  /// wider than the label is scaled down to fit. Returns false when the printer
  /// is not ready or the job could not be handed off to the print server.
  bool print_label(const std::string &line);

 protected:
  void on_connected() override;
  void on_disconnected() override;

 private:
  // Bulk transfers are queued strictly one at a time: the ESP-IDF HCD rejects a
  // second concurrent URB on the same non-periodic endpoint with
  // ESP_ERR_INVALID_STATE, so MAX_INFLIGHT must stay 1. 4 KiB chunks match the
  // reference ZP450 print server and keep the submit/completion round trips low.
  static constexpr size_t CHUNK_SIZE = 4096;
  static constexpr size_t CHUNK_COUNT = 8;
  static constexpr size_t MAX_INFLIGHT = 1;

  // IEEE-1284 status-byte bits reported by the printer's GET_PORT_STATUS.
  static constexpr uint8_t STATUS_PAPER_EMPTY = 0x20;  // bit 5
  static constexpr uint8_t STATUS_SELECT = 0x10;       // bit 4
  static constexpr uint8_t STATUS_NOT_ERROR = 0x08;    // bit 3 (1 = no error)

  struct Chunk {
    uint8_t data[CHUNK_SIZE];
    size_t len;
  };

  struct OutXferCtx {
    DymoPrintServer *self;
    Chunk *chunk;
  };

  bool find_printer_interface_();
  void select_alt_setting_(uint8_t alt);
  void printer_class_port_status_();

  static void tcp_task_entry_(void *arg);
  void tcp_task_();
  void handle_client_(int fd);
  /// Send a finished byte stream to the local raw-9100 listener (127.0.0.1).
  bool send_raw_(const uint8_t *data, size_t len);
  void start_output_();
  void start_input_();
  static void out_transfer_cb_(usb_transfer_t *xfer);

  uint16_t port_{9100};

  std::atomic<uint8_t> iface_{0};
  std::atomic<uint8_t> alt_setting_{0};
  std::atomic<uint8_t> ep_out_{0};
  std::atomic<uint8_t> ep_in_{0};
  std::atomic<bool> claimed_{false};
  std::atomic<bool> printer_ready_{false};
  std::atomic<bool> status_inflight_{false};
  std::atomic<uint8_t> printer_status_{0};
  std::atomic<bool> has_status_{false};
  // Number of printer-class alternate settings found; > 1 means the interface
  // must be switched with SET_INTERFACE after claiming.
  uint8_t alt_count_{0};
  bool status_polling_enabled_{true};
  std::atomic<size_t> inflight_{0};
  std::atomic<uint32_t> generation_{0};
  std::atomic<uint32_t> out_bytes_{0};
  uint16_t ep_out_mps_{64};
  uint16_t ep_in_mps_{64};

  Chunk chunks_[CHUNK_COUNT];
  QueueHandle_t free_queue_{nullptr};
  QueueHandle_t ready_queue_{nullptr};
  // Serializes the ready-queue check and the transfer submit so the byte stream
  // is never reordered or dropped when both the TCP task and the USB completion
  // callback try to feed the pipe at once.
  SemaphoreHandle_t out_mutex_{nullptr};
  TaskHandle_t tcp_task_handle_{nullptr};
  bool tcp_started_{false};
};

}  // namespace esphome::dymo_printserver
