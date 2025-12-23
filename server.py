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
        
        threading.Thread(target=self.game_loop, daemon=True).start()

    def load_map(self, filename):
        walls = set()
        try:
            with open(filename, 'r') as f:
                lines = f.readlines()
                for y, line in enumerate(lines):
                    for x, char in enumerate(line.strip()):
                        if char == '1': walls.add((x, y))
        except FileNotFoundError:
            print("Error: map.txt not found. Using empty map.")
        return walls

    def add_log(self, message):
        timestamp = time.strftime("%H:%M:%S", time.localtime())
        formatted_msg = f"[{timestamp}] {message}"
        print(formatted_msg)
        self.logs.append(formatted_msg)
        if len(self.logs) > 12: self.logs.pop(0)

    def is_valid_spawn(self, x, y):
        """Kiểm tra vị trí đứng có hợp lệ không (không trùng tường, không trùng người)"""
        if not (0 <= x < MAP_WIDTH and 0 <= y < MAP_HEIGHT): return False
        if (x, y) in self.map_data: return False
        for p in self.players.values():
            if p["x"] == x and p["y"] == y: return False
        return True

    def get_valid_directions(self, x, y):
        """
        Hàm mới: Trả về danh sách các hướng mà tại (x,y) có thể nhìn được 
        (tức là ô trước mặt không phải là tường)
        """
        valid_dirs = []
        # Check UP (0)
        if (x, y - 1) not in self.map_data and 0 <= y - 1 < MAP_HEIGHT:
            valid_dirs.append(0)
        # Check DOWN (1)
        if (x, y + 1) not in self.map_data and 0 <= y + 1 < MAP_HEIGHT:
            valid_dirs.append(1)
        # Check LEFT (2)
        if (x - 1, y) not in self.map_data and 0 <= x - 1 < MAP_WIDTH:
            valid_dirs.append(2)
        # Check RIGHT (3)
        if (x + 1, y) not in self.map_data and 0 <= x + 1 < MAP_WIDTH:
            valid_dirs.append(3)
        return valid_dirs

    def remove_player(self, player_id, reason="left"):
        with self.lock:
            if player_id in self.players:
                name = self.players[player_id]['name']
                del self.players[player_id]
                if reason == "crash":
                    self.add_log(f"Connection lost: {name} (timeout/crash).")
                else:
                    self.add_log(f"Player {name} has left the game.")

    def handle_hit(self, shooter_id, victim_id):
        shooter = self.players.get(shooter_id)
        victim = self.players.get(victim_id)
        
        if shooter and victim:
            shooter["score"] += 11
            victim["score"] -= 5
            self.add_log(f"{shooter['name']} killed {victim['name']} (+11pts).")
            
            # --- LOGIC RESPAWN MỚI ---
            while True:
                # 1. Random vị trí đứng
                rx, ry = random.randint(1, MAP_WIDTH - 2), random.randint(1, MAP_HEIGHT - 2)
                
                # 2. Nếu vị trí đứng ok -> Kiểm tra các hướng nhìn
                if self.is_valid_spawn(rx, ry):
                    valid_dirs = self.get_valid_directions(rx, ry)
                    
                    # 3. Nếu có ít nhất 1 hướng nhìn không bị tường chặn -> Chọn
                    if valid_dirs:
                        victim["x"], victim["y"] = rx, ry
                        victim["direction"] = random.choice(valid_dirs)
                        break
                    # Nếu bị vây kín 4 bề là tường (valid_dirs rỗng) -> Loop lại tìm chỗ khác
            
            self.add_log(f"{victim['name']} respawned at ({victim['x']}, {victim['y']}).")

    # --- RPC IMPLEMENTATION ---

    def Join(self, request, context):
        with self.lock:
            # --- LOGIC SPAWN MỚI ---
            rx, ry, r_dir = 1, 1, 0
            
            while True:
                # 1. Random vị trí
                rx = random.randint(1, MAP_WIDTH - 2)
                ry = random.randint(1, MAP_HEIGHT - 2)
                
                # 2. Check vị trí đứng
                if self.is_valid_spawn(rx, ry):
                    # 3. Lấy danh sách hướng hợp lệ (không úp mặt vào tường)
                    valid_dirs = self.get_valid_directions(rx, ry)
                    
                    if valid_dirs:
                        r_dir = random.choice(valid_dirs)
                        break
                    # Nếu không có hướng nào (vị trí xấu) -> Random lại
            
            player_id = f"{request.name}_{int(time.time())}"
            self.players[player_id] = {
                "name": request.name, "x": rx, "y": ry, 
                "score": 0, "direction": r_dir, "last_shot": 0
            }
            self.add_log(f"Player {request.name} joined the game.")
            
            wall_list = [coord for pt in self.map_data for coord in pt]
            
        return game_pb2.JoinResponse(
            player_id=player_id, start_x=rx, start_y=ry, 
            start_direction=r_dir, walls=wall_list
        )

    def Leave(self, request, context):
        self.remove_player(request.player_id, reason="left")
        return game_pb2.LeaveResponse(success=True)

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

            if (nx, ny) in self.map_data: 
                return game_pb2.MoveResponse(success=False)
            
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
            
            if time.time() - p["last_shot"] < 0.5: 
                return game_pb2.ShootResponse(success=False)
            
            p["last_shot"] = time.time()
            p["score"] -= 1 
            
            bx, by = p["x"], p["y"]
            d = p["direction"]
            if d == 0: by -= 1
            elif d == 1: by += 1
            elif d == 2: bx -= 1
            elif d == 3: bx += 1
            
            self.add_log(f"{p['name']} fired a bullet.")
            
            # Instant Hit Check
            hit_victim_id = None
            for pid, other in self.players.items():
                if pid != request.player_id and other["x"] == bx and other["y"] == by:
                    hit_victim_id = pid
                    break
            
            if hit_victim_id:
                self.handle_hit(request.player_id, hit_victim_id)
                return game_pb2.ShootResponse(success=True)
            
            if (bx, by) in self.map_data: 
                return game_pb2.ShootResponse(success=True)
            
            new_bullet = Bullet(request.player_id, bx, by, d)
            self.bullets.append(new_bullet)
            return game_pb2.ShootResponse(success=True)

    def game_loop(self):
        while True:
            time.sleep(0.05) 
            with self.lock: 
                dead_bullets = []
                for b in self.bullets:
                    if b.direction == 0: b.y -= 1
                    elif b.direction == 1: b.y += 1
                    elif b.direction == 2: b.x -= 1
                    elif b.direction == 3: b.x += 1
                    
                    b.distance_traveled += 1

                    if (b.x, b.y) in self.map_data or not (0 <= b.x < MAP_WIDTH and 0 <= b.y < MAP_HEIGHT):
                        dead_bullets.append(b); continue
                    
                    hit_victim_id = None
                    for pid, p in self.players.items():
                        if pid != b.owner_id and p["x"] == b.x and p["y"] == b.y:
                            hit_victim_id = pid; break
                    
                    if hit_victim_id:
                        dead_bullets.append(b)
                        self.handle_hit(b.owner_id, hit_victim_id)
                        continue
                    
                    if b.distance_traveled > 32: dead_bullets.append(b)
                
                for db in dead_bullets:
                    if db in self.bullets: self.bullets.remove(db)

    def GetGameState(self, request, context):
        player_id = request.player_id
        try:
            while context.is_active(): 
                resp = game_pb2.GameState()
                with self.lock:
                    if player_id not in self.players: break 

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
        except Exception as e:
            print(f"Stream error for {player_id}: {e}")
        finally:
            print(f"Cleaning up disconnected player: {player_id}")
            self.remove_player(player_id, reason="crash")

def serve():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=20))
    game_pb2_grpc.add_GameServiceServicer_to_server(MazeGameServer(), server)
    server.add_insecure_port('[::]:50051')
    print("Server started on port 50051...")
    server.start()
    server.wait_for_termination()

if __name__ == '__main__':
    serve()