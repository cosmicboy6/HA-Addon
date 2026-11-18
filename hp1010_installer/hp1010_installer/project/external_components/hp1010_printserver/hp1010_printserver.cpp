#include "hp1010_printserver.h"

#include <cerrno>
#include <cstring>
#include <new>

#include <arpa/inet.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <unistd.h>

namespace esphome::hp1010_printserver {

static const char *const TAG = "hp1010_printserver";

// USB Printer Class (see usb.org "USB Printer Class Specification"). The
// standard subclass is 0x01 (Printer), but a few devices report 0x00/0xFF, so
// only the class is required - the device is already bound by VID/PID.
static constexpr uint8_t HP_PRINTER_CLASS = 0x07;

// Descriptor types and endpoint transfer types from usb_types_ch9.h, spelled
// out here so the component does not depend on the ESP-IDF macro names.
static constexpr uint8_t HP_DESC_INTERFACE = 0x04;
static constexpr uint8_t HP_DESC_ENDPOINT = 0x05;
static constexpr uint8_t HP_XFER_TYPE_MASK = 0x03;
static constexpr uint8_t HP_XFER_TYPE_BULK = 0x02;

// PJL (Printer Job Language) page-count query. The leading/trailing UEL
// (universal exit language, ESC%-12345X) brackets the command so it is parsed
// as a PJL job rather than as print data. The printer answers on the bulk IN
// back-channel with "@PJL INFO PAGECOUNT\r\n<digits>\r\n\x0c".
static constexpr const char *PJL_PAGECOUNT_QUERY = "\x1b%-12345X@PJL INFO PAGECOUNT\r\n\x1b%-12345X";

void HP1010PrintServer::loop() {
  if (!this->tcp_started_) {
    this->tcp_started_ = true;
    // The network stack is only initialized once the main loop runs; calling
    // socket()/bind() from setup() faults the device, so start the listener here.
    if (xTaskCreatePinnedToCore(tcp_task_entry_, "hp1010_tcp", 8192, this, 4, &this->tcp_task_handle_, 0) != pdPASS) {
      ESP_LOGE(TAG, "Failed to create TCP task");
      this->mark_failed();
    }
  }
  if (!this->process_usb_events_())
    this->disable_loop();
}

void HP1010PrintServer::setup() {
  usb_host::USBClient::setup();
  if (this->is_failed())
    return;

  this->free_queue_ = xQueueCreate(CHUNK_COUNT, sizeof(Chunk *));
  this->ready_queue_ = xQueueCreate(CHUNK_COUNT, sizeof(Chunk *));
  this->out_mutex_ = xSemaphoreCreateMutex();
  if (this->free_queue_ == nullptr || this->ready_queue_ == nullptr || this->out_mutex_ == nullptr) {
    ESP_LOGE(TAG, "Failed to create chunk queues");
    this->mark_failed();
    return;
  }
  for (auto &chunk : this->chunks_) {
    Chunk *p = &chunk;
    xQueueSend(this->free_queue_, &p, 0);
  }

  // Poll the printer's USB Printer Class status so a stalled, deselected, or
  // out-of-paper printer is visible; a raw 9100 bridge otherwise never notices.
  // Not all printers answer GET_PORT_STATUS meaningfully, so it is optional.
  if (this->status_polling_enabled_) {
    this->set_interval("printer_status", 15000, [this]() { this->printer_class_port_status_(); });
  }

  // Refresh the PJL page count shortly after each job (the TCP task raises
  // page_count_refresh_) and at least every 5 minutes. Runs from the main loop
  // because set_timeout() inside query_page_count_() is not task-safe. Only the
  // HP LaserJet speaks PJL; sending the query to another printer would inject
  // control bytes into its print stream, so such configs set page_count: false.
  if (this->page_count_enabled_) {
    this->set_interval("page_count", 10000, [this]() {
      const bool requested = this->page_count_refresh_.load(std::memory_order_acquire);
      const uint32_t now = millis();
      if ((requested || now - this->last_page_count_query_ > 300000) && this->query_page_count_()) {
        this->page_count_refresh_.store(false, std::memory_order_release);
        this->last_page_count_query_ = now;
      }
    });
  }
}

void HP1010PrintServer::dump_config() {
  ESP_LOGCONFIG(TAG, "USB raw-9100 print server:");
  ESP_LOGCONFIG(TAG, "  TCP port: %u", this->port_);
  if (this->is_ready()) {
    ESP_LOGCONFIG(TAG, "  USB interface: %u alt %u", this->iface_.load(), this->alt_setting_.load());
    ESP_LOGCONFIG(TAG, "  Bulk OUT: 0x%02X (MPS %u)", this->ep_out_.load(), this->ep_out_mps_);
    if (this->ep_in_.load() != 0)
      ESP_LOGCONFIG(TAG, "  Bulk IN: 0x%02X", this->ep_in_.load());
    if (this->has_status())
      ESP_LOGCONFIG(TAG, "  Port status: 0x%02X (PaperEmpty=%u Select=%u NotError=%u)", this->printer_status(),
                    (this->printer_status() >> 5) & 0x01, (this->printer_status() >> 4) & 0x01,
                    (this->printer_status() >> 3) & 0x01);
    if (this->has_page_count())
      ESP_LOGCONFIG(TAG, "  Page count: %u", this->page_count());
  } else {
    ESP_LOGCONFIG(TAG, "  Printer: not connected");
  }
  ESP_LOGCONFIG(TAG, "  PJL page count: %s", this->page_count_enabled_ ? "enabled" : "disabled");
  ESP_LOGCONFIG(TAG, "  Status polling: %s", this->status_polling_enabled_ ? "enabled" : "disabled");
  usb_host::USBClient::dump_config();
}

// Walk the configuration descriptor and pick the best printer-class interface
// alternate setting that has a bulk OUT endpoint. Printers vary widely:
//   * HP LaserJet 1010: one interface, three alternates - alt 0 is IEEE 1284.4
//     packet mode (protocol 3, where raw PCL is parsed as MLC and discarded),
//     alt 1 bidirectional (protocol 2), alt 2 unidirectional (protocol 1).
//   * DYMO LabelWriter 450: a single interface/alternate (protocol 2).
// Preference order is bidirectional > unidirectional > unspecified > 1284.4, so
// the HP gets its bidirectional setting and the DYMO keeps its only setting.
bool HP1010PrintServer::find_printer_interface_() {
  const usb_config_desc_t *config_desc;
  if (usb_host_get_active_config_descriptor(this->device_handle_, &config_desc) != ESP_OK) {
    ESP_LOGW(TAG, "Could not read configuration descriptor");
    return false;
  }

  const uint8_t *base = reinterpret_cast<const uint8_t *>(config_desc);
  const size_t total = config_desc->wTotalLength;

  uint8_t best_iface = 0;
  uint8_t best_alt = 0;
  uint8_t best_out = 0;
  uint8_t best_in = 0;
  uint16_t best_out_mps = 64;
  uint16_t best_in_mps = 64;
  int best_score = -1;
  uint8_t alt_count = 0;

  // Candidate accumulated from one interface descriptor and the endpoints that
  // follow it, until the next interface descriptor starts a new candidate.
  bool have_candidate = false;
  bool candidate_is_printer = false;
  uint8_t cand_iface = 0;
  uint8_t cand_alt = 0;
  uint8_t cand_protocol = 0;
  uint8_t cand_out = 0;
  uint8_t cand_in = 0;
  uint16_t cand_out_mps = 64;
  uint16_t cand_in_mps = 64;

  auto flush_candidate = [&]() {
    if (!have_candidate || !candidate_is_printer)
      return;
    alt_count++;
    if (cand_out == 0)
      return;
    int score;
    switch (cand_protocol) {
      case 2: score = 30; break;  // bidirectional
      case 1: score = 20; break;  // unidirectional
      case 0: score = 10; break;  // unspecified
      case 3: score = 0; break;   // IEEE 1284.4 packet mode
      default: score = 5; break;
    }
    if (score > best_score) {
      best_score = score;
      best_iface = cand_iface;
      best_alt = cand_alt;
      best_out = cand_out;
      best_in = cand_in;
      best_out_mps = cand_out_mps;
      best_in_mps = cand_in_mps;
    }
  };

  size_t offset = 0;
  while (offset + 2 <= total) {
    const auto *desc = reinterpret_cast<const usb_standard_desc_t *>(base + offset);
    if (desc->bLength < 2 || offset + desc->bLength > total)
      break;

    if (desc->bDescriptorType == HP_DESC_INTERFACE) {
      flush_candidate();
      const auto *intf = reinterpret_cast<const usb_intf_desc_t *>(desc);
      have_candidate = true;
      candidate_is_printer = intf->bInterfaceClass == HP_PRINTER_CLASS;
      cand_iface = intf->bInterfaceNumber;
      cand_alt = intf->bAlternateSetting;
      cand_protocol = intf->bInterfaceProtocol;
      cand_out = 0;
      cand_in = 0;
      cand_out_mps = 64;
      cand_in_mps = 64;
      if (candidate_is_printer) {
        ESP_LOGD(TAG, "Printer interface %u alt %u, class 0x%02X sub 0x%02X protocol 0x%02X, endpoints %u",
                 cand_iface, cand_alt, intf->bInterfaceClass, intf->bInterfaceSubClass, intf->bInterfaceProtocol,
                 intf->bNumEndpoints);
      }
    } else if (desc->bDescriptorType == HP_DESC_ENDPOINT && have_candidate && candidate_is_printer) {
      const auto *ep = reinterpret_cast<const usb_ep_desc_t *>(desc);
      if ((ep->bmAttributes & HP_XFER_TYPE_MASK) == HP_XFER_TYPE_BULK) {
        if ((ep->bEndpointAddress & 0x80) != 0) {
          cand_in = ep->bEndpointAddress;
          cand_in_mps = ep->wMaxPacketSize;
        } else if (cand_out == 0) {
          cand_out = ep->bEndpointAddress;
          cand_out_mps = ep->wMaxPacketSize;
        }
      }
    }
    offset += desc->bLength;
  }
  flush_candidate();

  if (best_score < 0) {
    ESP_LOGW(TAG, "No bulk OUT endpoint on a printer interface found");
    return false;
  }

  this->iface_.store(best_iface);
  this->alt_setting_.store(best_alt);
  this->ep_out_.store(best_out);
  this->ep_in_.store(best_in);
  this->ep_out_mps_ = best_out_mps;
  this->ep_in_mps_ = best_in_mps;
  this->alt_count_ = alt_count;
  ESP_LOGI(TAG, "Printer interface %u alt %u selected (bulk OUT 0x%02X, bulk IN 0x%02X, %u alternate setting(s))",
           best_iface, best_alt, best_out, best_in, alt_count);
  return true;
}

// Select the printer's alternate interface setting. The HP LaserJet 1010 powers
// up on alt 0 (IEEE 1284.4 packet mode, protocol 3), where raw PCL is parsed as
// MLC packets and silently discarded; the CUPS usb backend deliberately ignores
// protocol 3 and selects the bidirectional setting, then calls SET_INTERFACE. A
// printer with a single alternate setting (e.g. the DYMO LabelWriter 450) is
// already where it needs to be and is left alone. Standard SET_INTERFACE:
// bmRequestType 0x01, bRequest 0x0B.
void HP1010PrintServer::select_alt_setting_(uint8_t alt) {
  const uint8_t iface = this->iface_.load();
  const bool ok = this->control_transfer(
      usb_host::USB_TYPE_STANDARD | usb_host::USB_RECIP_INTERFACE, 0x0B, alt, iface,
      [alt](const usb_host::TransferStatus &result) {
        if (result.error_code != 0)
          ESP_LOGW(TAG, "SET_INTERFACE alt %u failed (status=%d)", alt, result.error_code);
        else
          ESP_LOGD(TAG, "SET_INTERFACE alt %u -> status=%d", alt, result.error_code);
      });
  if (!ok)
    ESP_LOGW(TAG, "Failed to submit SET_INTERFACE alt %u", alt);
}

// Ask the printer for its USB Printer Class port status. The bits are the
// IEEE-1284 status byte: bit5 PaperEmpty, bit4 Select, bit3 NotError (1 = no
// error). Only one class control request may be in flight at a time.
void HP1010PrintServer::printer_class_port_status_() {
  if (!this->is_ready() || this->status_inflight_.exchange(true, std::memory_order_acq_rel))
    return;
  const uint8_t iface = this->iface_.load();
  // GET_PORT_STATUS: bmRequestType class/interface/device-to-host, bRequest 0x01.
  std::vector<uint8_t> one(1, 0);
  const bool ok = this->control_transfer(
      usb_host::USB_DIR_IN | usb_host::USB_TYPE_CLASS | usb_host::USB_RECIP_INTERFACE, 0x01, 0, iface,
      [this](const usb_host::TransferStatus &result) {
        this->status_inflight_.store(false, std::memory_order_release);
        // The reply buffer starts with the 8-byte setup packet, then the data.
        if (result.success && result.data_len > usb_host::SETUP_PACKET_SIZE) {
          const uint8_t s = result.data[usb_host::SETUP_PACKET_SIZE];
          this->printer_status_.store(s, std::memory_order_release);
          this->has_status_.store(true, std::memory_order_release);
          ESP_LOGD(TAG, "Printer port status 0x%02X (PaperEmpty=%u Select=%u NotError=%u)", s, (s >> 5) & 0x01,
                   (s >> 4) & 0x01, (s >> 3) & 0x01);
        } else {
          ESP_LOGW(TAG, "GET_PORT_STATUS failed (status=%u, len=%u)", result.error_code,
                   static_cast<unsigned>(result.data_len));
        }
      },
      one);
  if (!ok) {
    this->status_inflight_.store(false, std::memory_order_release);
    ESP_LOGW(TAG, "Failed to submit GET_PORT_STATUS");
  }
}

// Ask the printer for its lifetime page count with a PJL INFO PAGECOUNT query.
// The command is injected into the normal bulk-OUT print pipeline (the same
// chunk queue and transfer path used for print data) and the reply is captured
// by the bulk IN drain. Reusing the proven print path avoids a second, raw
// usb_host_transfer_alloc()/submit() consumer on the same endpoint, which
// wedged the USB host task and tripped the Task WDT.
//
// Safety: a PJL command injected into a print stream would be parsed as part of
// the job, so this only queues while the pipe is genuinely idle (no client, no
// queued/in-flight chunks). out_mutex_ makes the idle test and the queue push
// atomic with respect to start_output_().
bool HP1010PrintServer::query_page_count_() {
  if (!this->is_ready() || this->pjl_waiting_.load(std::memory_order_acquire))
    return false;

  if (xSemaphoreTake(this->out_mutex_, pdMS_TO_TICKS(100)) != pdTRUE)
    return false;

  bool queued = false;
  const bool idle = !this->client_active_.load(std::memory_order_acquire) &&
                    this->inflight_.load(std::memory_order_acquire) == 0 &&
                    uxQueueMessagesWaiting(this->ready_queue_) == 0;

  if (idle) {
    Chunk *chunk = nullptr;
    if (xQueueReceive(this->free_queue_, &chunk, 0) == pdTRUE && chunk != nullptr) {
      const size_t len = strlen(PJL_PAGECOUNT_QUERY);
      if (len <= CHUNK_SIZE) {
        memcpy(chunk->data, PJL_PAGECOUNT_QUERY, len);
        chunk->len = len;
        this->pjl_len_ = 0;
        this->pjl_waiting_.store(true, std::memory_order_release);
        if (xQueueSend(this->ready_queue_, &chunk, 0) == pdTRUE) {
          queued = true;
          ESP_LOGD(TAG, "PJL page-count query queued (%u bytes)", static_cast<unsigned>(len));
          // Give up if the printer never answers, so the next query can run.
          this->set_timeout("pjl_timeout", 3000, [this]() {
            this->pjl_waiting_.store(false, std::memory_order_release);
          });
        } else {
          xQueueSend(this->free_queue_, &chunk, 0);
          this->pjl_waiting_.store(false, std::memory_order_release);
        }
      } else {
        xQueueSend(this->free_queue_, &chunk, 0);
      }
    }
  }

  xSemaphoreGive(this->out_mutex_);
  if (queued)
    this->start_output_();
  return queued;
}

// Called from the bulk IN drain when a PJL reply is being collected. The LJ1010
// answers with the value on the line after the command name:
//   "@PJL INFO PAGECOUNT\r\n44032\r\n\x0c"
// so skip past "PAGECOUNT" to the first run of digits.
void HP1010PrintServer::parse_pjl_page_count_() {
  char *p = strstr(this->pjl_buffer_, "PAGECOUNT");
  if (p == nullptr)
    return;
  p += strlen("PAGECOUNT");
  while (*p != '\0' && (*p < '0' || *p > '9'))
    p++;
  if (*p < '0' || *p > '9')
    return;
  uint32_t value = 0;
  while (*p >= '0' && *p <= '9') {
    value = value * 10 + static_cast<uint32_t>(*p - '0');
    p++;
  }
  this->page_count_.store(value, std::memory_order_release);
  this->has_page_count_.store(true, std::memory_order_release);
  this->pjl_waiting_.store(false, std::memory_order_release);
  ESP_LOGI(TAG, "Printer page count: %u", value);
}

void HP1010PrintServer::on_connected() {
  this->claimed_.store(false);
  this->iface_.store(0);
  this->ep_out_.store(0);
  this->ep_in_.store(0);
  this->pjl_waiting_.store(false, std::memory_order_release);
  this->pjl_len_ = 0;

  if (!this->find_printer_interface_()) {
    ESP_LOGW(TAG, "Device is not a usable USB printer");
    this->status_set_warning(LOG_STR("No printer interface"));
    this->disconnect();
    return;
  }

  const uint8_t iface = this->iface_.load();

  esp_err_t err = usb_host_interface_claim(this->handle_, this->device_handle_, iface, 0);
  if (err != ESP_OK) {
    ESP_LOGE(TAG, "usb_host_interface_claim(%u) failed: %s", iface, esp_err_to_name(err));
    this->status_set_error(LOG_STR("Printer interface claim failed"));
    this->disconnect();
    return;
  }
  this->claimed_.store(true);

  // Switch to the alternate setting that actually carries raw print data. Only
  // needed when the interface offers more than one; single-setting printers are
  // already on the right one.
  if (this->alt_count_ > 1) {
    this->select_alt_setting_(this->alt_setting_.load());
  }

  // A new connection invalidates anything left over from a previous job.
  this->generation_.fetch_add(1, std::memory_order_acq_rel);
  Chunk *stale = nullptr;
  while (xQueueReceive(this->ready_queue_, &stale, 0) == pdTRUE)
    xQueueSend(this->free_queue_, &stale, 0);

  this->status_clear_error();
  this->status_clear_warning();
  this->printer_ready_.store(true, std::memory_order_release);
  ESP_LOGI(TAG, "Printer ready (interface %u, bulk OUT 0x%02X, bulk IN 0x%02X)", iface, this->ep_out_.load(),
           this->ep_in_.load());
  this->start_output_();

  // Continuously drain the reverse (bulk IN) channel. In bidirectional mode the
  // printer reports status/back-channel data here and can stop accepting print
  // data if nobody reads it. This mirrors the CUPS usb backend, which selects the
  // bidirectional setting and polls the reverse channel while printing.
  this->set_timeout("usb_in", 500, [this]() {
    if (this->is_ready())
      this->start_input_();
  });

  // Read the printer's status soon after connecting (the interval handler only
  // fires every 15 s). Delayed so it cannot race the SET_INTERFACE above.
  if (this->status_polling_enabled_) {
    this->set_timeout("printer_status_first", 2000, [this]() {
      if (this->is_ready())
        this->printer_class_port_status_();
    });
  }

  // Read the page count once the interface has settled (HP PJL only).
  if (this->page_count_enabled_) {
    this->set_timeout("page_count_first", 4000, [this]() {
      if (this->is_ready() && this->query_page_count_())
        this->last_page_count_query_ = millis();
    });
  }
}

void HP1010PrintServer::on_disconnected() {
  this->printer_ready_.store(false, std::memory_order_release);
  this->status_inflight_.store(false, std::memory_order_release);
  this->has_status_.store(false, std::memory_order_release);
  this->pjl_waiting_.store(false, std::memory_order_release);
  this->pjl_len_ = 0;
  this->generation_.fetch_add(1, std::memory_order_acq_rel);

  // The TCP task notices the generation change on its next recv() timeout or
  // read and closes its own socket; on_disconnected() never closes the fd.
  const uint8_t ep_out = this->ep_out_.exchange(0);
  const uint8_t ep_in = this->ep_in_.exchange(0);
  const uint8_t iface = this->iface_.exchange(0);
  if (this->claimed_.exchange(false) && this->device_handle_ != nullptr) {
    if (ep_out != 0) {
      usb_host_endpoint_halt(this->device_handle_, ep_out);
      usb_host_endpoint_flush(this->device_handle_, ep_out);
    }
    if (ep_in != 0) {
      usb_host_endpoint_halt(this->device_handle_, ep_in);
      usb_host_endpoint_flush(this->device_handle_, ep_in);
    }
    usb_host_interface_release(this->handle_, this->device_handle_, iface);
  }

  if (this->ready_queue_ != nullptr) {
    Chunk *stale = nullptr;
    while (xQueueReceive(this->ready_queue_, &stale, 0) == pdTRUE)
      xQueueSend(this->free_queue_, &stale, 0);
  }

  this->status_set_warning(LOG_STR("Printer disconnected"));
  usb_host::USBClient::on_disconnected();
}

void HP1010PrintServer::start_output_() {
  if (!this->is_ready())
    return;
  const uint8_t ep_out = this->ep_out_.load();
  if (ep_out == 0)
    return;

  // The TCP task and the USB completion callback both call this. The check of
  // inflight_ and the dequeue/submit that follows must be atomic: otherwise two
  // callers can both pass the inflight_ test, take two chunks and submit them
  // out of order (or one submit fails and its chunk is lost), corrupting the
  // print stream. Serialize the whole sequence.
  if (xSemaphoreTake(this->out_mutex_, portMAX_DELAY) != pdTRUE)
    return;

  while (this->inflight_.load(std::memory_order_acquire) < MAX_INFLIGHT) {
    Chunk *chunk = nullptr;
    if (xQueueReceive(this->ready_queue_, &chunk, 0) != pdTRUE)
      break;

    this->inflight_.fetch_add(1, std::memory_order_acq_rel);

    usb_transfer_t *xfer = nullptr;
    if (usb_host_transfer_alloc(chunk->len, 0, &xfer) != ESP_OK || xfer == nullptr) {
      ESP_LOGW(TAG, "Bulk OUT transfer alloc failed");
      this->inflight_.fetch_sub(1, std::memory_order_acq_rel);
      xQueueSend(this->free_queue_, &chunk, 0);
      break;
    }

    memcpy(xfer->data_buffer, chunk->data, chunk->len);
    xfer->num_bytes = chunk->len;
    xfer->device_handle = this->device_handle_;
    xfer->bEndpointAddress = ep_out;
    xfer->timeout_ms = 5000;

    auto *ctx = new (std::nothrow) OutXferCtx{this, chunk};
    if (ctx == nullptr) {
      ESP_LOGW(TAG, "Bulk OUT context alloc failed");
      usb_host_transfer_free(xfer);
      this->inflight_.fetch_sub(1, std::memory_order_acq_rel);
      xQueueSend(this->free_queue_, &chunk, 0);
      break;
    }
    xfer->callback = &HP1010PrintServer::out_transfer_cb_;
    xfer->context = ctx;

    const esp_err_t err = usb_host_transfer_submit(xfer);
    if (err != ESP_OK) {
      ESP_LOGW(TAG, "Bulk OUT submit failed: %s", esp_err_to_name(err));
      delete ctx;
      usb_host_transfer_free(xfer);
      this->inflight_.fetch_sub(1, std::memory_order_acq_rel);
      xQueueSend(this->free_queue_, &chunk, 0);
      // The endpoint may be halted from a previous error; try to clear it so
      // the next chunk has a chance.
      usb_host_endpoint_clear(this->device_handle_, ep_out);
      break;
    }
  }

  xSemaphoreGive(this->out_mutex_);
}

void HP1010PrintServer::out_transfer_cb_(usb_transfer_t *xfer) {
  auto *ctx = static_cast<OutXferCtx *>(xfer->context);
  auto *this_ = ctx->self;
  Chunk *chunk = ctx->chunk;
  delete ctx;

  if (xfer->status != USB_TRANSFER_STATUS_COMPLETED) {
    ESP_LOGW(TAG, "Bulk OUT transfer error status=%d, actual=%d", xfer->status, xfer->actual_num_bytes);
    // The printer may have stalled the endpoint; clear it so the next chunk
    // can be submitted.
    const uint8_t ep_out = this_->ep_out_.load();
    if (ep_out != 0 && this_->device_handle_ != nullptr) {
      usb_host_endpoint_clear(this_->device_handle_, ep_out);
    }
  } else {
    this_->out_bytes_.fetch_add(static_cast<uint32_t>(xfer->actual_num_bytes), std::memory_order_relaxed);
  }

  usb_host_transfer_free(xfer);
  if (chunk != nullptr) {
    xQueueSend(this_->free_queue_, &chunk, 0);
  }
  this_->inflight_.fetch_sub(1, std::memory_order_acq_rel);
  this_->start_output_();
}

void HP1010PrintServer::start_input_() {
  const uint8_t ep_in = this->ep_in_.load();
  if (ep_in == 0 || !this->is_ready())
    return;

  const bool ok = this->transfer_in(
      ep_in,
      [this](const usb_host::TransferStatus &status) {
        if (!this->is_ready())
          return;  // disconnected; don't restart
        if (status.success && status.data_len > 0) {
          // While a PJL query is outstanding, collect the back-channel bytes so
          // the page count can be parsed out of them.
          if (this->pjl_waiting_.load(std::memory_order_acquire)) {
            const size_t room = sizeof(this->pjl_buffer_) - 1 - this->pjl_len_;
            const size_t n = status.data_len < room ? status.data_len : room;
            if (n > 0) {
              memcpy(this->pjl_buffer_ + this->pjl_len_, status.data, n);
              this->pjl_len_ += n;
              this->pjl_buffer_[this->pjl_len_] = '\0';
              ESP_LOGD(TAG, "PJL reply (%u bytes): %s", static_cast<unsigned>(this->pjl_len_), this->pjl_buffer_);
              // A form feed terminates a PJL response.
              if (memchr(this->pjl_buffer_, 0x0C, this->pjl_len_) != nullptr)
                this->pjl_waiting_.store(false, std::memory_order_release);
              this->parse_pjl_page_count_();
            }
          }
          char buf[128];
          size_t pos = 0;
          for (size_t i = 0; i < status.data_len && pos < sizeof(buf) - 4; i++)
            pos += snprintf(buf + pos, sizeof(buf) - pos, "%02X ", status.data[i]);
          ESP_LOGV(TAG, "Printer back-channel (%u bytes): %s", status.data_len, buf);
        }
        // Keep the bulk-IN pipe active so the printer can report status/ready state.
        this->start_input_();
      },
      this->ep_in_mps_);
  if (!ok)
    ESP_LOGV(TAG, "Failed to start bulk IN transfer");
}

void HP1010PrintServer::tcp_task_entry_(void *arg) { static_cast<HP1010PrintServer *>(arg)->tcp_task_(); }

void HP1010PrintServer::tcp_task_() {
  const int listen_fd = socket(AF_INET, SOCK_STREAM, 0);
  if (listen_fd < 0) {
    ESP_LOGE(TAG, "socket() failed: %d", errno);
    vTaskDelete(nullptr);
    return;
  }
  int one = 1;
  setsockopt(listen_fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));

  struct sockaddr_in addr {};
  addr.sin_family = AF_INET;
  addr.sin_addr.s_addr = htonl(INADDR_ANY);
  addr.sin_port = htons(this->port_);
  if (bind(listen_fd, reinterpret_cast<struct sockaddr *>(&addr), sizeof(addr)) < 0) {
    ESP_LOGE(TAG, "bind(%u) failed: %d", this->port_, errno);
    close(listen_fd);
    vTaskDelete(nullptr);
    return;
  }
  if (listen(listen_fd, 2) < 0) {
    ESP_LOGE(TAG, "listen(%u) failed: %d", this->port_, errno);
    close(listen_fd);
    vTaskDelete(nullptr);
    return;
  }
  ESP_LOGI(TAG, "Raw print server listening on port %u", this->port_);

  for (;;) {
    struct sockaddr_in client_addr {};
    socklen_t client_len = sizeof(client_addr);
    const int fd = accept(listen_fd, reinterpret_cast<struct sockaddr *>(&client_addr), &client_len);
    if (fd < 0) {
      if (errno != EINTR)
        ESP_LOGW(TAG, "accept failed: %d", errno);
      vTaskDelay(pdMS_TO_TICKS(500));
      continue;
    }
    ESP_LOGI(TAG, "Print client connected: %s", inet_ntoa(client_addr.sin_addr));
    this->handle_client_(fd);
    close(fd);
    ESP_LOGI(TAG, "Print client disconnected");
  }
}

void HP1010PrintServer::handle_client_(int fd) {
  if (!this->is_ready()) {
    ESP_LOGW(TAG, "Printer not ready, rejecting job");
    return;
  }

  // Blocks the PJL page-count query for the duration of the job so no PJL
  // command is ever interleaved into the print stream.
  this->client_active_.store(true, std::memory_order_release);

  int nodelay = 1;
  setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &nodelay, sizeof(nodelay));
  struct timeval timeout {};
  timeout.tv_sec = 5;
  setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));

  const uint32_t gen = this->generation_.load(std::memory_order_acquire);
  this->out_bytes_.store(0, std::memory_order_relaxed);
  uint32_t recv_total = 0;

  // --- Streaming phase ------------------------------------------------------
  while (this->is_ready() && this->generation_.load(std::memory_order_acquire) == gen) {
    Chunk *chunk = nullptr;
    if (xQueueReceive(this->free_queue_, &chunk, pdMS_TO_TICKS(2000)) != pdTRUE)
      continue;

    const int n = recv(fd, chunk->data, CHUNK_SIZE, 0);
    if (n <= 0) {
      xQueueSend(this->free_queue_, &chunk, 0);
      if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK))
        continue;  // receive timeout: re-check printer state
      break;       // peer closed or hard error
    }

    if (!this->is_ready() || this->generation_.load(std::memory_order_acquire) != gen) {
      xQueueSend(this->free_queue_, &chunk, 0);
      break;
    }

    chunk->len = static_cast<size_t>(n);
    recv_total += static_cast<uint32_t>(n);
    ESP_LOGV(TAG, "Received %d bytes from TCP", n);
    if (xQueueSend(this->ready_queue_, &chunk, pdMS_TO_TICKS(2000)) != pdTRUE) {
      xQueueSend(this->free_queue_, &chunk, 0);
      ESP_LOGW(TAG, "Output queue full, dropping job");
      break;
    }
    this->start_output_();
  }

  // Flush the remainder of this job before accepting another client.
  const uint32_t drain_start = millis();
  while ((uxQueueMessagesWaiting(this->ready_queue_) > 0 || this->inflight_.load(std::memory_order_acquire) > 0) &&
         this->generation_.load(std::memory_order_acquire) == gen && millis() - drain_start < 30000) {
    this->start_output_();
    vTaskDelay(pdMS_TO_TICKS(10));
  }
  const bool drain_timeout = (uxQueueMessagesWaiting(this->ready_queue_) > 0 || this->inflight_.load(std::memory_order_acquire) > 0);
  ESP_LOGI(TAG, "Job done: received %u bytes over TCP, forwarded %u bytes to USB%s", static_cast<unsigned>(recv_total),
           static_cast<unsigned>(this->out_bytes_.load(std::memory_order_relaxed)), drain_timeout ? " (DRAIN TIMEOUT)" : "");

  this->client_active_.store(false, std::memory_order_release);
  // Ask the main loop to re-read the page count now that the pipe is idle.
  this->page_count_refresh_.store(true, std::memory_order_release);
}

}  // namespace esphome::hp1010_printserver
