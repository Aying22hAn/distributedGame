import grpc
from concurrent import futures
import time
import threading
import random
import game_pb2
import game_pb2_grpc

# Cấu hình
MAP_WIDTH = 32
MAP_HEIGHT = 16
# Tăng tốc độ đạn lên một chút để mượt hơn
BULLET_SPEED = 0.05 

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
        self.players = {} 
        self.bullets = []
        self.logs = []
        self.map_data = self.load_map("map.txt")
        self.lock = threading.Lock()
        
        # Luồng chạy game loop (xử lý đạn bay)
        threading.Thread(target=self.game_loop, daemon=True).start()

    def load_map(self, filename):
        walls = set()
        try:
            with open(filename, 'r') as f:
                lines = f.readlines()
                for y, line in enumerate(lines):
                    for x, char in enumerate(line.strip()):
                        if char == '1':
                            walls.add((x, y))
        except FileNotFoundError:
            print("Map file not found! Using empty map.")
        return walls

    def add_log(self, message):
        """Thêm log vào danh sách và xóa bớt nếu quá dài"""
        timestamp = time.strftime("%H:%M:%S", time.localtime())
        formatted_msg = f"[{timestamp}] {message}"
        print(formatted_msg) # In ra server console để debug
        self.logs.append(formatted_msg)
        if len(self.logs) > 12: # Giữ 12 log mới nhất cho client
            self.logs.pop(0)

    def is_valid_spawn(self, x, y):
        if not (0 <= x < MAP_WIDTH and 0 <= y < MAP_HEIGHT): return False
        if (x, y) in self.map_data: return False
        for p in self.players.values():
            if p["x"] == x and p["y"] == y: return False
        return True

    def handle_hit(self, shooter_id, victim_id):
        """Hàm xử lý logic khi một người chơi bị bắn trúng"""
        shooter = self.players.get(shooter_id)
        victim = self.players.get(victim_id)
        
        if shooter and victim:
            # 1. Cập nhật điểm
            shooter["score"] += 11
            victim["score"] -= 5
            
            # 2. Log chi tiết
            self.add_log(f"💥 {shooter['name']} ĐÃ TIÊU DIỆT {victim['name']}! (+11pts)")
            
            # 3. Respawn victim (Random vị trí mới)
            while True:
                rx, ry = random.randint(1, MAP_WIDTH - 2), random.randint(1, MAP_HEIGHT - 2)
                if self.is_valid_spawn(rx, ry):
                    victim["x"], victim["y"] = rx, ry
                    victim["direction"] = random.choice([0,1,2,3])
                    break
            
            self.add_log(f"🚑 {victim['name']} hồi sinh tại ({victim['x']}, {victim['y']})")

    def Join(self, request, context):
        with self.lock:
            while True:
                rx = random.randint(1, MAP_WIDTH - 2)
                ry = random.randint(1, MAP_HEIGHT - 2)
                if self.is_valid_spawn(rx, ry):
                    break
            
            r_dir = random.choice([0, 1, 2, 3])
            player_id = f"{request.name}_{int(time.time())}"
            
            self.players[player_id] = {
                "name": request.name, "x": rx, "y": ry, 
                "score": 0, "direction": r_dir, "last_shot": 0
            }
            
            self.add_log(f"👋 Người chơi {request.name} đã tham gia game.")
            
            wall_list = [coord for pt in self.map_data for coord in pt]
            
        return game_pb2.JoinResponse(
            player_id=player_id, start_x=rx, start_y=ry, 
            start_direction=r_dir, walls=wall_list
        )

    def Move(self, request, context):
        with self.lock:
            if request.player_id not in self.players:
                return game_pb2.MoveResponse(success=False)
            
            p = self.players[request.player_id]
            p["direction"] = request.direction 
            
            nx, ny = p["x"], p["y"]
            if request.direction == 0: ny -= 1
            elif request.direction == 1: ny += 1
            elif request.direction == 2: nx -= 1
            elif request.direction == 3: nx += 1

            if (nx, ny) in self.map_data: return game_pb2.MoveResponse(success=False)
            
            for pid, other in self.players.items():
                if pid != request.player_id and other["x"] == nx and other["y"] == ny:
                    return game_pb2.MoveResponse(success=False)
            
            p["x"], p["y"] = nx, ny
            return game_pb2.MoveResponse(success=True)

    def Shoot(self, request, context):
        with self.lock:
            if request.player_id not in self.players:
                return game_pb2.ShootResponse(success=False)
            
            p = self.players[request.player_id]
            # Cooldown 0.5s
            if time.time() - p["last_shot"] < 0.5:
                 return game_pb2.ShootResponse(success=False)

            p["last_shot"] = time.time()
            p["score"] -= 1
            
            # Tính tọa độ viên đạn sẽ xuất hiện
            bx, by = p["x"], p["y"]
            d = p["direction"]
            if d == 0: by -= 1
            elif d == 1: by += 1
            elif d == 2: bx -= 1
            elif d == 3: bx += 1

            self.add_log(f"🔫 {p['name']} vừa bắn một viên đạn!")

            # --- SỬA LỖI ĐI XUYÊN TƯỚNG ---
            # Kiểm tra ngay lập tức xem ô trước mặt có địch không (Instant Hit)
            hit_victim_id = None
            for pid, other in self.players.items():
                if pid != request.player_id and other["x"] == bx and other["y"] == by:
                    hit_victim_id = pid
                    break
            
            if hit_victim_id:
                # Nếu có địch ngay trước mặt, xử lý trúng đạn luôn, không cần tạo Bullet object
                self.handle_hit(request.player_id, hit_victim_id)
                return game_pb2.ShootResponse(success=True)

            # Nếu đụng tường ngay lập tức
            if (bx, by) in self.map_data:
                return game_pb2.ShootResponse(success=True)

            # Nếu ô trống -> Tạo đạn bay bình thường
            new_bullet = Bullet(request.player_id, bx, by, d)
            self.bullets.append(new_bullet)
            return game_pb2.ShootResponse(success=True)

    def game_loop(self):
        while True:
            time.sleep(0.05) # 20 ticks/second
            with self.lock:
                dead_bullets = []
                for b in self.bullets:
                    # Di chuyển đạn
                    if b.direction == 0: b.y -= 1
                    elif b.direction == 1: b.y += 1
                    elif b.direction == 2: b.x -= 1
                    elif b.direction == 3: b.x += 1
                    
                    b.distance_traveled += 1

                    # 1. Check tường hoặc ra khỏi map
                    if (b.x, b.y) in self.map_data or not (0 <= b.x < MAP_WIDTH and 0 <= b.y < MAP_HEIGHT):
                        dead_bullets.append(b)
                        continue
                    
                    # 2. Check trúng người (Logic xử lý đạn bay từ xa)
                    hit_victim_id = None
                    for pid, p in self.players.items():
                        # Đạn không trúng người bắn (trừ khi đạn nảy - game này ko có nảy)
                        if pid != b.owner_id and p["x"] == b.x and p["y"] == b.y:
                            hit_victim_id = pid
                            break
                    
                    if hit_victim_id:
                        dead_bullets.append(b)
                        self.handle_hit(b.owner_id, hit_victim_id)
                        continue
                    
                    # Giới hạn tầm xa đạn (ví dụ 32 ô)
                    if b.distance_traveled > 32:
                         dead_bullets.append(b)
                
                for db in dead_bullets:
                    if db in self.bullets: self.bullets.remove(db)

    def GetGameState(self, request, context):
        while True:
            resp = game_pb2.GameState()
            with self.lock:
                for pid, p in self.players.items():
                    p_entry = resp.players[pid]
                    p_entry.name = p["name"]
                    p_entry.x = p["x"]
                    p_entry.y = p["y"]
                    p_entry.score = p["score"]
                    p_entry.direction = p["direction"]
                
                for b in self.bullets:
                    b_entry = resp.bullets.add()
                    b_entry.id = b.id
                    b_entry.x = b.x
                    b_entry.y = b.y
                    b_entry.direction = b.direction
                
                resp.logs.extend(self.logs)

            yield resp
            time.sleep(0.05)

def serve():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    game_pb2_grpc.add_GameServiceServicer_to_server(MazeGameServer(), server)
    server.add_insecure_port('[::]:50051')
    print("Server started on port 50051...")
    server.start()
    server.wait_for_termination()

if __name__ == '__main__':
    serve()