import grpc
from concurrent import futures
import time
import threading
import random
import game_pb2
import game_pb2_grpc

# --- CẤU HÌNH SERVER ---
MAP_WIDTH = 32
MAP_HEIGHT = 16
BULLET_SPEED = 0.05  # Tốc độ cập nhật đạn (giây)

# Class đại diện cho viên đạn trên Server
class Bullet:
    def __init__(self, owner_id, x, y, direction):
        self.id = f"{owner_id}_{time.time()}"
        self.owner_id = owner_id
        self.x = x
        self.y = y
        self.direction = direction
        self.distance_traveled = 0

class MazeGameServer(game_pb2_grpc.GameServiceServicer):
    def __init__(self):
        # Global State: Chứa toàn bộ thông tin của game
        self.players = {} # Lưu trữ thông tin người chơi
        self.bullets = [] # Lưu trữ danh sách đạn đang bay
        self.logs = []    # Lưu trữ nhật ký hoạt động (Logs)
        
        # Load bản đồ từ file map.txt
        self.map_data = self.load_map("map.txt")
        
        # --- MUTUAL EXCLUSION (LOẠI TRỪ LẪN NHAU) ---
        # Khóa này đảm bảo tại một thời điểm chỉ có 1 luồng được thay đổi State.
        # Ngăn chặn Race Condition khi nhiều client cùng di chuyển hoặc bắn.
        self.lock = threading.Lock()
        
        # Bắt đầu luồng chạy vòng lặp game độc lập (xử lý đạn bay)
        threading.Thread(target=self.game_loop, daemon=True).start()

    def load_map(self, filename):
        """Đọc file map và lưu vị trí các bức tường"""
        walls = set()
        try:
            with open(filename, 'r') as f:
                lines = f.readlines()
                for y, line in enumerate(lines):
                    # Đọc từng ký tự, nếu là '1' thì coi là tường
                    for x, char in enumerate(line.strip()):
                        if char == '1': walls.add((x, y))
        except FileNotFoundError:
            print("Error: map.txt not found. Using empty map.")
        return walls

    def add_log(self, message):
        """Thêm log mới và duy trì tối đa 12 dòng log"""
        # Format: [HH:MM:SS] Message
        timestamp = time.strftime("%H:%M:%S", time.localtime())
        formatted_msg = f"[{timestamp}] {message}"
        print(formatted_msg) # In ra màn hình console của Server
        
        self.logs.append(formatted_msg)
        if len(self.logs) > 12: # Giữ kích thước log cố định để gửi về Client
            self.logs.pop(0)

    def is_valid_spawn(self, x, y):
        """Kiểm tra vị trí (x, y) có hợp lệ để sinh ra (Spawn) không"""
        # 1. Kiểm tra biên bản đồ
        if not (0 <= x < MAP_WIDTH and 0 <= y < MAP_HEIGHT): return False
        # 2. Kiểm tra có trùng tường không
        if (x, y) in self.map_data: return False
        # 3. Kiểm tra có trùng người chơi khác đang đứng không
        for p in self.players.values():
            if p["x"] == x and p["y"] == y: return False
        return True

    def remove_player(self, player_id, reason="left"):
        """Xóa người chơi khỏi game (Mutual Exclusion được xử lý ở nơi gọi hàm)"""
        with self.lock: # Đảm bảo thread-safe khi xóa
            if player_id in self.players:
                name = self.players[player_id]['name']
                del self.players[player_id]
                if reason == "crash":
                    self.add_log(f"Connection lost: {name} (timeout/crash).")
                else:
                    self.add_log(f"Player {name} has left the game.")

    def handle_hit(self, shooter_id, victim_id):
        """Xử lý logic khi bắn trúng: Cộng/Trừ điểm và Respawn"""
        shooter = self.players.get(shooter_id)
        victim = self.players.get(victim_id)
        
        if shooter and victim:
            # Cập nhật điểm số
            shooter["score"] += 11
            victim["score"] -= 5
            
            self.add_log(f"{shooter['name']} killed {victim['name']} (+11pts).")
            
            # Hồi sinh nạn nhân tại vị trí ngẫu nhiên mới
            while True:
                rx, ry = random.randint(1, MAP_WIDTH - 2), random.randint(1, MAP_HEIGHT - 2)
                if self.is_valid_spawn(rx, ry):
                    victim["x"], victim["y"] = rx, ry
                    victim["direction"] = random.choice([0,1,2,3])
                    break
            self.add_log(f"{victim['name']} respawned at ({victim['x']}, {victim['y']}).")

    # --- RPC IMPLEMENTATION ---

    def Join(self, request, context):
        """Xử lý yêu cầu tham gia game"""
        with self.lock:
            # Tìm vị trí spawn ngẫu nhiên
            while True:
                rx = random.randint(1, MAP_WIDTH - 2)
                ry = random.randint(1, MAP_HEIGHT - 2)
                if self.is_valid_spawn(rx, ry): break
            
            r_dir = random.choice([0, 1, 2, 3])
            # Tạo ID duy nhất cho người chơi
            player_id = f"{request.name}_{int(time.time())}"
            
            # Lưu vào Global State
            self.players[player_id] = {
                "name": request.name, "x": rx, "y": ry, 
                "score": 0, "direction": r_dir, "last_shot": 0
            }
            self.add_log(f"Player {request.name} joined the game.")
            
            # Chuyển dữ liệu tường thành mảng phẳng (flat list) để gửi qua gRPC
            wall_list = [coord for pt in self.map_data for coord in pt]
            
        return game_pb2.JoinResponse(
            player_id=player_id, start_x=rx, start_y=ry, 
            start_direction=r_dir, walls=wall_list
        )

    def Leave(self, request, context):
        """Người chơi chủ động thoát"""
        self.remove_player(request.player_id, reason="left")
        return game_pb2.LeaveResponse(success=True)

    def Move(self, request, context):
        """Xử lý di chuyển với Mutual Exclusion"""
        with self.lock: # Bắt đầu miền găng (Critical Section)
            if request.player_id not in self.players:
                return game_pb2.MoveResponse(success=False)
            
            p = self.players[request.player_id] 
            
            # Tính toán vị trí dự kiến
            nx, ny = p["x"], p["y"]
            if request.direction == 0 and p["direction"] != 1: ny -= 1   # UP
            elif request.direction == 1 and p["direction"] != 0: ny += 1 # DOWN
            elif request.direction == 2 and p["direction"] != 3: nx -= 1 # LEFT
            elif request.direction == 3 and p["direction"] != 2: nx += 1 # RIGHT

            # Luôn cập nhật hướng quay mặt
            p["direction"] = request.direction 
            
            # 1. Check va chạm Tường
            if (nx, ny) in self.map_data: 
                return game_pb2.MoveResponse(success=False)
            
            # 2. Check va chạm Người chơi khác (Mutual Exclusion Rule)
            for pid, other in self.players.items():
                if pid != request.player_id and other["x"] == nx and other["y"] == ny:
                    return game_pb2.MoveResponse(success=False)
            
            # Nếu hợp lệ -> Cập nhật vị trí
            p["x"], p["y"] = nx, ny
            return game_pb2.MoveResponse(success=True)

    def Shoot(self, request, context):
        """Xử lý bắn súng"""
        with self.lock:
            if request.player_id not in self.players: 
                return game_pb2.ShootResponse(success=False)
            
            p = self.players[request.player_id]
            
            # Kiểm tra Cooldown (0.5s)
            if time.time() - p["last_shot"] < 0.5: 
                return game_pb2.ShootResponse(success=False)
            
            p["last_shot"] = time.time()
            p["score"] -= 1 # Phạt 1 điểm khi bắn
            
            # Tính vị trí xuất hiện của đạn (ngay trước mặt)
            bx, by = p["x"], p["y"]
            d = p["direction"]
            if d == 0: by -= 1
            elif d == 1: by += 1
            elif d == 2: bx -= 1
            elif d == 3: bx += 1
            
            self.add_log(f"{p['name']} fired a bullet.")
            
            # --- INSTANT HIT CHECK (KIỂM TRA TRÚNG NGAY LẬP TỨC) ---
            # Nếu địch đứng ngay trước mặt, xử lý trúng luôn, không cần tạo Bullet object
            hit_victim_id = None
            for pid, other in self.players.items():
                if pid != request.player_id and other["x"] == bx and other["y"] == by:
                    hit_victim_id = pid
                    break
            
            if hit_victim_id:
                self.handle_hit(request.player_id, hit_victim_id)
                return game_pb2.ShootResponse(success=True)
            
            # Nếu bắn vào tường ngay lập tức
            if (bx, by) in self.map_data: 
                return game_pb2.ShootResponse(success=True)
            
            # Nếu ô trống -> Tạo đối tượng Bullet để bay
            new_bullet = Bullet(request.player_id, bx, by, d)
            self.bullets.append(new_bullet)
            return game_pb2.ShootResponse(success=True)

    def game_loop(self):
        """Vòng lặp cập nhật trạng thái đạn (Physics Loop)"""
        while True:
            time.sleep(0.05) # 20 ticks/second
            with self.lock: # Đảm bảo tính nhất quán khi cập nhật Global State
                dead_bullets = []
                for b in self.bullets:
                    # Di chuyển đạn
                    if b.direction == 0: b.y -= 1
                    elif b.direction == 1: b.y += 1
                    elif b.direction == 2: b.x -= 1
                    elif b.direction == 3: b.x += 1
                    
                    b.distance_traveled += 1

                    # Check va chạm tường hoặc ra khỏi map
                    if (b.x, b.y) in self.map_data or not (0 <= b.x < MAP_WIDTH and 0 <= b.y < MAP_HEIGHT):
                        dead_bullets.append(b); continue
                    
                    # Check trúng người chơi
                    hit_victim_id = None
                    for pid, p in self.players.items():
                        if pid != b.owner_id and p["x"] == b.x and p["y"] == b.y:
                            hit_victim_id = pid; break
                    
                    if hit_victim_id:
                        dead_bullets.append(b)
                        self.handle_hit(b.owner_id, hit_victim_id)
                        continue
                    
                    # Giới hạn tầm xa đạn (32 ô)
                    if b.distance_traveled > 32: dead_bullets.append(b)
                
                # Dọn dẹp đạn đã chết
                for db in dead_bullets:
                    if db in self.bullets: self.bullets.remove(db)

    def GetGameState(self, request, context):
        """Streaming State về Client. Xử lý disconnection tại đây."""
        player_id = request.player_id
        try:
            while context.is_active(): # Kiểm tra kết nối
                resp = game_pb2.GameState()
                with self.lock:
                    # Nếu player đã bị xóa thì dừng stream
                    if player_id not in self.players: break 

                    # Đóng gói danh sách người chơi
                    for pid, p in self.players.items():
                        p_entry = resp.players[pid]
                        p_entry.name = p["name"]
                        p_entry.x = p["x"]
                        p_entry.y = p["y"]
                        p_entry.score = p["score"]
                        p_entry.direction = p["direction"]
                    
                    # Đóng gói danh sách đạn
                    for b in self.bullets:
                        b_entry = resp.bullets.add()
                        b_entry.id = b.id
                        b_entry.x = b.x
                        b_entry.y = b.y
                        b_entry.direction = b.direction
                    
                    # Đóng gói logs
                    resp.logs.extend(self.logs)
                
                yield resp # Gửi gói tin về Client
                time.sleep(0.05) # Tần suất gửi (20 lần/giây)
        except Exception as e:
            print(f"Stream error for {player_id}: {e}")
        finally:
            # Nếu vòng lặp gãy (do Client Crash/Tắt mạng), xóa người chơi
            print(f"Cleaning up disconnected player: {player_id}")
            self.remove_player(player_id, reason="crash")

def serve():
    # Sử dụng ThreadPool để xử lý nhiều Client đồng thời
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=20))
    game_pb2_grpc.add_GameServiceServicer_to_server(MazeGameServer(), server)
    server.add_insecure_port('[::]:50051')
    print("Server started on port 50051...")
    server.start()
    server.wait_for_termination()

if __name__ == '__main__':
    serve()