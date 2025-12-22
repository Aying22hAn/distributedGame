import grpc
import pygame
import threading
import sys
import game_pb2
import game_pb2_grpc

# --- CẤU HÌNH UI (UPDATED SIZE) ---
# Tăng kích thước Cell để màn hình to hơn, dễ nhìn hơn
CELL_SIZE = 30  
MAP_W, MAP_H = 32, 16

# Kích thước vùng chơi game (960x480)
GAME_W = MAP_W * CELL_SIZE 
GAME_H = MAP_H * CELL_SIZE

# Kích thước thanh bên (Sidebar) rộng hơn để chứa Log tiếng Anh
SIDEBAR_W = 300 

WINDOW_W = GAME_W + SIDEBAR_W
WINDOW_H = GAME_H

# Định nghĩa màu sắc (R, G, B)
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
GRAY = (50, 50, 50)     # Màu tường
RED = (200, 50, 50)     # Màu địch
GREEN = (50, 200, 50)   # Màu mình
YELLOW = (255, 255, 0)  # Màu đạn và text nổi bật
BLUE_BG = (30, 30, 50)  # Màu nền sidebar
BUTTON_COLOR = (200, 50, 50)
BUTTON_HOVER = (255, 100, 100)

class GameClient:
    def __init__(self):
        pygame.init()
        self.screen = pygame.display.set_mode((WINDOW_W, WINDOW_H))
        pygame.display.set_caption("Distributed Maze War (gRPC)")
        
        # Font chữ
        self.font = pygame.font.Font(None, 24)
        self.log_font = pygame.font.Font(None, 20) # Font nhỏ hơn cho Log để không tràn
        self.big_font = pygame.font.Font(None, 40)
        self.clock = pygame.time.Clock()
        
        # Kết nối gRPC
        self.channel = grpc.insecure_channel('localhost:50051')
        self.stub = game_pb2_grpc.GameServiceStub(self.channel)
        
        # Biến trạng thái client
        self.my_id = None
        self.walls = set() # Cache vị trí tường để không phải nhận liên tục
        self.state = None  # Lưu state mới nhất từ server
        self.running = True

        # Nút Exit nằm ở góc dưới bên phải
        self.exit_btn_rect = pygame.Rect(GAME_W + 75, WINDOW_H - 60, 150, 40)

    def show_login_screen(self):
        """Hiển thị màn hình nhập tên trước khi vào game"""
        input_box = pygame.Rect(WINDOW_W//2 - 100, WINDOW_H//2 - 20, 200, 40)
        color_inactive = pygame.Color('lightskyblue3')
        color_active = pygame.Color('dodgerblue2')
        color = color_inactive
        active = False
        text = ''
        done = False

        while not done:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    pygame.quit(); sys.exit()
                if event.type == pygame.MOUSEBUTTONDOWN:
                    if input_box.collidepoint(event.pos):
                        active = not active
                    else:
                        active = False
                    color = color_active if active else color_inactive
                if event.type == pygame.KEYDOWN:
                    if active:
                        if event.key == pygame.K_RETURN:
                            if text.strip(): return text
                        elif event.key == pygame.K_BACKSPACE:
                            text = text[:-1]
                        else:
                            text += event.unicode

            self.screen.fill((30, 30, 30))
            # Render text box
            txt_surface = self.font.render(text, True, color)
            width = max(200, txt_surface.get_width()+10)
            input_box.w = width
            self.screen.blit(txt_surface, (input_box.x+5, input_box.y+5))
            pygame.draw.rect(self.screen, color, input_box, 2)
            
            prompt = self.big_font.render("Enter Name & Press Enter:", True, WHITE)
            self.screen.blit(prompt, (WINDOW_W//2 - 180, WINDOW_H//2 - 70))
            
            pygame.display.flip()
            self.clock.tick(30)

    def join_game(self, name):
        """Gọi RPC Join để tham gia"""
        try:
            resp = self.stub.Join(game_pb2.JoinRequest(name=name))
            self.my_id = resp.player_id
            
            # Giải nén thông tin tường (tối ưu hóa băng thông)
            w_list = resp.walls
            for i in range(0, len(w_list), 2):
                self.walls.add((w_list[i], w_list[i+1]))
            
            print(f"Joined as {name} (ID: {self.my_id})")
        except grpc.RpcError:
            print("Cannot connect to server!")
            pygame.quit(); sys.exit()

    def listen_thread(self):
        """Luồng chạy nền để nhận Stream State từ Server"""
        try:
            request = game_pb2.GameStateRequest(player_id=self.my_id)
            for s in self.stub.GetGameState(request):
                self.state = s
                # Tự kiểm tra xem mình có bị Server xóa chưa (Kick/Crash logic)
                if self.my_id not in s.players:
                    print("You have been removed from the game.")
                    self.running = False
                    break
        except grpc.RpcError:
            print("Lost connection to server.")
            self.running = False

    def leave_game(self):
        """Gửi RPC Leave khi bấm nút thoát"""
        if self.my_id:
            try:
                self.stub.Leave(game_pb2.LeaveRequest(player_id=self.my_id))
                print("Left game successfully.")
            except grpc.RpcError:
                pass

    def draw_player_triangle(self, x, y, direction, color):
        """Vẽ tam giác chỉ hướng người chơi"""
        center_x = x * CELL_SIZE + CELL_SIZE // 2
        center_y = y * CELL_SIZE + CELL_SIZE // 2
        offset = CELL_SIZE // 2 - 4 # Kích thước tam giác
        
        # Tính toán 3 đỉnh tam giác dựa trên hướng
        if direction == 0: # UP
            p1 = (center_x, center_y - offset)
            p2 = (center_x - offset + 4, center_y + offset)
            p3 = (center_x + offset - 4, center_y + offset)
        elif direction == 1: # DOWN
            p1 = (center_x, center_y + offset)
            p2 = (center_x - offset + 4, center_y - offset)
            p3 = (center_x + offset - 4, center_y - offset)
        elif direction == 2: # LEFT
            p1 = (center_x - offset, center_y)
            p2 = (center_x + offset, center_y - offset + 4)
            p3 = (center_x + offset, center_y + offset - 4)
        else: # RIGHT
            p1 = (center_x + offset, center_y)
            p2 = (center_x - offset, center_y - offset + 4)
            p3 = (center_x - offset, center_y + offset - 4)
            
        pygame.draw.polygon(self.screen, color, [p1, p2, p3])

    def draw(self):
        """Hàm vẽ chính (Render Loop)"""
        self.screen.fill(BLACK)
        
        # 1. Vẽ Tường (Dựa trên dữ liệu tĩnh đã load lúc Join)
        for (wx, wy) in self.walls:
            rect = pygame.Rect(wx*CELL_SIZE, wy*CELL_SIZE, CELL_SIZE, CELL_SIZE)
            pygame.draw.rect(self.screen, GRAY, rect)
            pygame.draw.rect(self.screen, (30,30,30), rect, 1) # Viền tường

        # Chỉ vẽ khi có State
        if self.state:
            # 2. Vẽ người chơi
            for pid, p in self.state.players.items():
                color = GREEN if pid == self.my_id else RED
                self.draw_player_triangle(p.x, p.y, p.direction, color)
                # Tên người chơi
                name_tag = self.font.render(p.name, True, WHITE)
                self.screen.blit(name_tag, (p.x*CELL_SIZE, p.y*CELL_SIZE - 15))

            # 3. Vẽ đạn
            for b in self.state.bullets:
                center = (b.x*CELL_SIZE + CELL_SIZE//2, b.y*CELL_SIZE + CELL_SIZE//2)
                pygame.draw.circle(self.screen, YELLOW, center, 5)

            # --- SIDEBAR (BẢNG THÔNG TIN) ---
            # Vẽ nền Sidebar
            pygame.draw.rect(self.screen, BLUE_BG, (GAME_W, 0, SIDEBAR_W, WINDOW_H))
            pygame.draw.line(self.screen, WHITE, (GAME_W, 0), (GAME_W, WINDOW_H), 2)
            
            # A. SCOREBOARD
            title = self.big_font.render("SCOREBOARD", True, YELLOW)
            self.screen.blit(title, (GAME_W + 20, 10))
            
            y_offset = 50
            # Sắp xếp điểm cao lên đầu
            sorted_players = sorted(self.state.players.values(), key=lambda x: x.score, reverse=True)
            for p in sorted_players:
                p_text = f"{p.name}: {p.score}"
                # Highlight tên mình
                col = GREEN if self.state.players.get(self.my_id) and p.name == self.state.players[self.my_id].name else WHITE
                surf = self.font.render(p_text, True, col)
                self.screen.blit(surf, (GAME_W + 20, y_offset))
                y_offset += 30

            # B. GAME LOGS
            # Vẽ Log thấp xuống một chút để tránh đè lên bảng điểm
            log_title = self.big_font.render("GAME LOGS", True, YELLOW)
            self.screen.blit(log_title, (GAME_W + 20, 260))
            
            y_log = 300
            # Lấy 12 dòng log từ server
            for line in self.state.logs: 
                # Sử dụng font nhỏ (20) để hiển thị nhiều thông tin hơn
                l_surf = self.log_font.render(line, True, (200, 200, 200))
                self.screen.blit(l_surf, (GAME_W + 10, y_log))
                y_log += 18 # Khoảng cách dòng xít hơn

        # 4. Vẽ nút EXIT
        mouse_pos = pygame.mouse.get_pos()
        btn_col = BUTTON_HOVER if self.exit_btn_rect.collidepoint(mouse_pos) else BUTTON_COLOR
        pygame.draw.rect(self.screen, btn_col, self.exit_btn_rect)
        exit_text = self.big_font.render("EXIT", True, WHITE)
        self.screen.blit(exit_text, (self.exit_btn_rect.x + 40, self.exit_btn_rect.y + 8))

        pygame.display.flip()

    def run(self):
        name = self.show_login_screen()
        self.join_game(name)
        
        # Khởi chạy luồng nghe dữ liệu ngầm
        threading.Thread(target=self.listen_thread, daemon=True).start()

        # Vòng lặp chính (Input + Render)
        while self.running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.leave_game()
                    self.running = False
                
                # Xử lý click chuột vào nút Exit
                if event.type == pygame.MOUSEBUTTONDOWN:
                    if self.exit_btn_rect.collidepoint(event.pos):
                        self.leave_game()
                        self.running = False

                # Xử lý phím bấm di chuyển/bắn
                if event.type == pygame.KEYDOWN:
                    d = None
                    if event.key == pygame.K_UP: d = 0
                    elif event.key == pygame.K_DOWN: d = 1
                    elif event.key == pygame.K_LEFT: d = 2
                    elif event.key == pygame.K_RIGHT: d = 3
                    
                    if d is not None:
                        try: self.stub.Move(game_pb2.MoveRequest(player_id=self.my_id, direction=d))
                        except: pass
                    
                    if event.key == pygame.K_SPACE:
                        try: self.stub.Shoot(game_pb2.ShootRequest(player_id=self.my_id))
                        except: pass

            self.draw()
            self.clock.tick(60) # Giới hạn 60 FPS
        
        pygame.quit()
        sys.exit()

if __name__ == "__main__":
    GameClient().run()